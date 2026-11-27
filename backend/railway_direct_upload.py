"""Private, temporary Railway bucket transport. Never an analyzer or Vault store."""
from __future__ import annotations

import json
import hashlib
import shutil
import tempfile
import logging
import os
import re
import threading
from collections.abc import Mapping
import time
from functools import lru_cache
from pathlib import Path
from tempfile import SpooledTemporaryFile
from uuid import UUID, uuid4

from fastapi import HTTPException, Request, Response
from fastapi.routing import APIRoute
from starlette.responses import FileResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import FormData, Headers, UploadFile

from backend.auth0_dependencies import bearer, get_current_user_optional
from backend.database import get_db
from backend.batch_processing import require_paid_batch_plan, BatchUploadPolicyError
from backend.subscriptions import get_user_entitlement
from backend.upload import MAX_UPLOAD_BYTES_BY_SUFFIX, MAX_PDF_TOOL_UPLOAD_BYTES, MAX_PDF_EDIT_ASSET_UPLOAD_BYTES
from backend.vault_upload_policy import VAULT_UPLOAD_LIMITS

log = logging.getLogger(__name__)
MB = 1024 * 1024
THRESHOLD = 4 * MB
DOWNLOAD_HEADER = 'x-redocx-bucket-download'
DOWNLOAD_PREFIX = 'redocx-delivery/v1/'
METADATA_FIELD = '_redocx_form_metadata'
MAX_METADATA_BYTES = 16 * MB
HEADER = 'x-redocx-staged-upload'
PREFIX = 'redocx-staging/v1/'
# Public routes are exactly those using optional Auth0 in the supplied router.
GUEST_FEATURES = frozenset({'convert','summarize','grammar-correct','translate','explain',
    'pdf/combine','pdf/split','pdf/edit/inspect','pdf/edit','pdf/compress','pdf/lock'})
SINGLE = frozenset({'convert','summarize','grammar-correct','translate','transcribe','text-to-speech',
    'vault','explain','generate-questions','generate-answers','redact','redact/detect-document-type',
    'redact/review','data-mask','data-mask/detect-document-type','data-mask/review',
    'pdf/split','pdf/edit/inspect','pdf/compress','pdf/lock','e-signature/layout'})
BATCH = frozenset('batch/'+x for x in ('convert','summarize','grammar-correct','translate','explain',
    'generate-questions','generate-answers','pdf/compress','pdf/lock','transcribe','vault'))
MULTI = {'structured-extraction': {'file','files'}, 'structured-extraction/review': {'files'},
    'compliance': {'file','files'}, 'compliance/set': {'files'}, 'compliance/preview': {'file','files'},
    'pdf/combine': {'files'}, 'pdf/edit': {'file','edit_assets'},
    'e-signature': {'file','files','signature_assets'}}
FIELDS = {**{x:{'file'} for x in SINGLE}, **{x:{'files'} for x in BATCH}, **MULTI}


def enabled():
    return os.getenv('RAILWAY_DIRECT_UPLOAD_ENABLED', '').lower() in ('1','true','yes')


def number(name, default):
    value = int(os.getenv(name, str(default)))
    if value <= 0:
        raise RuntimeError(f'{name} must be positive')
    return value


def row_values(row):
    return tuple(row.values()) if isinstance(row, Mapping) else tuple(row)


def error(status, code, message):
    return HTTPException(status, detail={'error':code, 'message':message})


@lru_cache(maxsize=1)
def storage():
    import boto3
    from botocore.config import Config
    endpoint = os.environ['RAILWAY_UPLOAD_BUCKET_ENDPOINT']
    if not endpoint.startswith('https://'):
        raise RuntimeError('Bucket endpoint must use HTTPS')
    style = os.getenv('RAILWAY_UPLOAD_BUCKET_ADDRESSING_STYLE','virtual')
    if style not in ('virtual','path'):
        raise RuntimeError('Invalid bucket addressing style')
    client = boto3.client('s3', endpoint_url=endpoint,
        aws_access_key_id=os.environ['RAILWAY_UPLOAD_BUCKET_ACCESS_KEY_ID'],
        aws_secret_access_key=os.environ['RAILWAY_UPLOAD_BUCKET_SECRET_ACCESS_KEY'],
        region_name=os.getenv('RAILWAY_UPLOAD_BUCKET_REGION','auto'),
        config=Config(signature_version='s3v4', s3={'addressing_style':style},
                      connect_timeout=10, read_timeout=60, retries={'max_attempts':2,'mode':'standard'},
                      request_checksum_calculation='when_required', response_checksum_validation='when_required'))
    return client, os.environ['RAILWAY_UPLOAD_BUCKET_NAME']


def object_key(ticket, index):
    return f'{PREFIX}{ticket}/{index}'


def owner_for(feature, request, user, response=None):
    if user is not None:
        return 'user:' + str(user.user_id)
    if feature not in GUEST_FEATURES:
        raise error(401,'authorization_required','Sign in to use this feature.')
    # Reuse the existing HttpOnly guest ownership cookie, including its secure policy.
    from backend.routes.route_v1 import _anonymous_artifact_owner_id
    return _anonymous_artifact_owner_id(request, response)


def vault_limits(user):
    e = get_user_entitlement(user.user_id)
    val = e.get if isinstance(e, Mapping) else lambda k,d=None: getattr(e,k,d)
    plan = str(val('plan','free')).strip().lower()
    if plan not in VAULT_UPLOAD_LIMITS or (plan != 'free' and val('is_paid') is not True):
        plan = 'free'
    return plan, VAULT_UPLOAD_LIMITS[plan]


def validate_manifest(feature, raw, user):
    if feature not in FIELDS or not isinstance(raw,list) or not 1 <= len(raw) <= 200:
        raise error(400,'invalid_staged_upload','Invalid upload selection.')
    vault = feature in ('vault','batch/vault')
    vault_plan, vault_policy = vault_limits(user) if vault else (None,None)
    if feature in BATCH and not vault:
        try:
            require_paid_batch_plan(get_user_entitlement(user.user_id))
        except BatchUploadPolicyError as exc:
            raise error(exc.status_code,exc.error_code,str(exc)) from exc
    result, counts, total = [], {}, 0
    for f in raw:
        if not isinstance(f,dict):
            raise error(400,'invalid_staged_upload','Invalid file metadata.')
        field,name,size,mime = (f.get(k) for k in ('field','filename','size','content_type'))
        if not isinstance(field,str) or field not in FIELDS[feature] | {METADATA_FIELD} or not isinstance(name,str) or not 1 <= len(name) <= 512 or any(ord(c)<32 for c in name) or not isinstance(mime,str) or len(mime)>200 or not re.fullmatch(r'[A-Za-z0-9!#$&^_.+/-]*',mime) or type(size) is not int or size<=0:
            raise error(400,'invalid_staged_upload','Invalid file metadata.')
        counts[field] = counts.get(field,0)+1
        if field in ('file', METADATA_FIELD) and counts[field]>1:
            raise error(400,'invalid_staged_upload','Only one file is allowed in this field.')
        suffix = Path(name).suffix.lower()
        if field == METADATA_FIELD:
            limit = MAX_METADATA_BYTES
        elif vault:
            limit = vault_policy[1]
        elif field in ('edit_assets','signature_assets'):
            limit = MAX_PDF_EDIT_ASSET_UPLOAD_BYTES if suffix in ('.png','.jpg','.jpeg') else 0
        elif feature.startswith('pdf/') or feature.startswith('batch/pdf/') or feature.startswith('e-signature'):
            limit = MAX_PDF_TOOL_UPLOAD_BYTES if suffix == '.pdf' else 0
        else:
            limit = MAX_UPLOAD_BYTES_BY_SUFFIX.get(suffix,0)
        if size > limit:
            if vault:
                raise error(403,f'VAULT_{vault_plan.upper()}_UPLOAD_SIZE_EXCEEDED','Vault size exceeds your plan.')
            raise error(413,'staged_upload_size_exceeded','File exceeds the allowed upload size or format.')
        total += size
        result.append({'field':field,'filename':name,'size':size,'content_type':mime or 'application/octet-stream'})
    if vault:
        count, cap = vault_policy
        if len(result)-counts.get(METADATA_FIELD,0)>count:
            raise error(403,f'VAULT_{vault_plan.upper()}_UPLOAD_COUNT_EXCEEDED','Vault file count exceeds your plan.')
        if sum(f['size'] for f in result if f['field'] != METADATA_FIELD)>cap:
            raise error(403,f'VAULT_{vault_plan.upper()}_UPLOAD_SIZE_EXCEEDED','Vault size exceeds your plan.')
    if (total <= THRESHOLD and not counts.get(METADATA_FIELD)) or total > number('RAILWAY_UPLOAD_MAX_REQUEST_BYTES', 10*1024*MB):
        raise error(413,'staged_upload_size_exceeded','Selection is outside the staged upload size limits.')
    return result,total


def issue_ticket(feature, raw, request, response, user):
    if not enabled():
        raise error(503,'staged_upload_unavailable','Large uploads are temporarily unavailable.')
    owner = owner_for(feature,request,user,response)
    manifest,total = validate_manifest(feature,raw,user)
    ticket = str(uuid4())
    ttl = number('RAILWAY_UPLOAD_URL_TTL_SECONDS',900)
    if ttl > 1800:
        raise RuntimeError('Upload URL lifetime must not exceed 1800 seconds')
    try:
        client,bucket = storage()
        # Exact-size signed POST policy, not an unrestricted PUT capability.
        posts = [client.generate_presigned_post(Bucket=bucket, Key=object_key(ticket,i),
            Fields={'Content-Type':'application/octet-stream'},
            Conditions=[{'Content-Type':'application/octet-stream'},['content-length-range',f['size'],f['size']]],
            ExpiresIn=ttl) for i,f in enumerate(manifest)]
    except Exception:
        log.warning('Staged upload signing failed')
        raise error(503,'staged_upload_unavailable','Could not authorize the upload. Try again.') from None
    # Serialize admission across all replicas. Reservations remain counted even after cleanup.
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT pg_advisory_xact_lock(723019462)')
            cur.execute("""SELECT COALESCE(SUM(total_bytes),0) AS global_bytes,
                COALESCE(SUM(total_bytes) FILTER (WHERE owner_id=%s),0) AS own_bytes,
                COALESCE(SUM(total_bytes) FILTER (WHERE owner_id LIKE 'anonymous:%%'),0) AS guest_bytes,
                COUNT(*) FILTER (WHERE owner_id=%s) AS own_count, COUNT(*) AS global_count
                FROM redocx_staged_uploads WHERE created_at > now()-interval '1 hour'""",(owner,owner))
            global_bytes,own_bytes,guest_bytes,own_count,global_count = row_values(cur.fetchone())
            own_limit = number('RAILWAY_UPLOAD_OWNER_HOURLY_BYTES',20*1024*MB) if user else number('RAILWAY_UPLOAD_GUEST_HOURLY_BYTES',500*MB)
            if (global_bytes+total>number('RAILWAY_UPLOAD_GLOBAL_HOURLY_BYTES',100*1024*MB)
                or own_bytes+total>own_limit or own_count>=number('RAILWAY_UPLOAD_OWNER_HOURLY_TICKETS',60)
                or global_count>=number('RAILWAY_UPLOAD_GLOBAL_HOURLY_TICKETS',2000)
                or (user is None and guest_bytes+total>number('RAILWAY_UPLOAD_GUEST_GLOBAL_HOURLY_BYTES',10*1024*MB))):
                raise error(429,'staged_upload_rate_limited','Large-upload capacity reached. Try again later.')
            cur.execute("""INSERT INTO redocx_staged_uploads(id,owner_id,feature,manifest,total_bytes,expires_at,cleanup_at)
                VALUES (%s,%s,%s,%s,%s,now()+%s*interval '1 second',now()+(%s+1800)*interval '1 second')""",
                (ticket,owner,feature,json.dumps(manifest),total,ttl,ttl))
        conn.commit()
    return {'upload_id':ticket,'uploads':posts,'expires_in':ttl}


def claim(ticket, owner, feature):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""UPDATE redocx_staged_uploads SET claimed=true,
                cleanup_at=GREATEST(cleanup_at,now()+interval '30 minutes')
                WHERE id=%s AND owner_id=%s AND feature=%s AND NOT claimed AND expires_at>now()
                RETURNING manifest""",(ticket,owner,feature))
            row = cur.fetchone()
        conn.commit()
    if row is None:
        raise error(409,'staged_upload_invalid','Upload expired, was already used, or does not belong to this session.')
    return json.loads(row_values(row)[0])


def delete_object(ticket,i):
    client,bucket = storage()
    client.delete_object(Bucket=bucket,Key=object_key(ticket,i))


def hydrate(ticket, manifest):
    """Bounded disk-backed streams; use actual bytes, never a caller-supplied URL."""
    client,bucket = storage()
    files=[]
    # The original ingestion pipeline creates additional quarantine/processing copies.
    required = 2 * sum(f['size'] for f in manifest) + number('RAILWAY_UPLOAD_FREE_DISK_RESERVE_BYTES', 256*MB)
    if shutil.disk_usage(tempfile.gettempdir()).free < required:
        raise error(503,'staged_upload_unavailable','Temporary processing storage is busy. Try a smaller selection.')
    deadline = time.monotonic()+number('RAILWAY_UPLOAD_FETCH_DEADLINE_SECONDS',1200)
    try:
        for i,f in enumerate(manifest):
            response = client.get_object(Bucket=bucket,Key=object_key(ticket,i))
            body = response['Body']
            stream = SpooledTemporaryFile(max_size=MB,mode='w+b')
            try:
                if response.get('ContentLength') != f['size']:
                    raise error(400,'staged_upload_size_mismatch','Uploaded file size differs from the reservation.')
                remaining=f['size']
                while remaining:
                    if time.monotonic()>deadline:
                        raise error(504,'staged_upload_timeout','Retrieving uploaded files took too long.')
                    chunk=body.read(min(MB,remaining))
                    if not chunk:
                        raise error(400,'staged_upload_incomplete','Uploaded file is incomplete.')
                    stream.write(chunk)
                    remaining-=len(chunk)
                if body.read(1):
                    raise error(400,'staged_upload_size_mismatch','Uploaded file exceeds the reservation.')
                stream.seek(0)
                files.append((f['field'],UploadFile(stream,size=f['size'],filename=f['filename'],
                    headers=Headers({'content-type':f['content_type']}))))
            except BaseException:
                stream.close()
                raise
            finally:
                body.close()
            # The expiry sweep repeats this delete after signed URLs expire, in case of replay.
            try:
                delete_object(ticket,i)
            except Exception:
                log.warning('Staged object immediate cleanup failed; expiry sweep will retry')
        return files
    except BaseException:
        for _,file in files:
            file.file.close()
        raise


class StagedUploadRoute(APIRoute):
    """Reconstitute ordinary UploadFile inputs before FastAPI's normal dependency/route handling."""
    def get_route_handler(self):
        original = super().get_route_handler()
        feature = self.path.split('/analyzer/',1)[-1]
        async def handler(request: Request):
            ticket = request.headers.get(HEADER)
            if ticket is None:
                result = await original(request)
                if (request.method == 'GET' and enabled() and request.headers.get(DOWNLOAD_HEADER) == '1'
                        and isinstance(result, FileResponse)
                        and (feature.startswith('artifacts/') or feature in ('e-signature/recipient/document','e-signature/completed/document'))):
                    owner = getattr(request.state,'redocx_download_owner',None)
                    if feature.startswith('e-signature/'):
                        owner = 'sign:' + str(request.headers.get('x-redocx-signing-token',''))
                    if not owner:
                        raise error(503,'staged_download_unavailable','Download authorization is unavailable.')
                    return await run_in_threadpool(maybe_stage_file_response, result, owner)
                return result
            if not enabled() or feature not in FIELDS or request.method != 'POST':
                raise error(400,'invalid_staged_upload','Staged uploads are not supported for this route.')
            try:
                if str(UUID(ticket)) != ticket:
                    raise ValueError()
            except ValueError:
                raise error(400,'invalid_staged_upload','Invalid upload reference.')
            user = await run_in_threadpool(get_current_user_optional,request,await bearer(request))
            owner = owner_for(feature,request,user)
            form = await request.form()
            files=[]
            try:
                if any(isinstance(value,UploadFile) or key in FIELDS[feature] | {METADATA_FIELD} for key,value in form.multi_items()):
                    raise error(400,'invalid_staged_upload','Do not mix staged files with inline upload fields.')
                manifest = await run_in_threadpool(claim,ticket,owner,feature)
                try:
                    files = await run_in_threadpool(hydrate,ticket,manifest)
                except HTTPException:
                    raise
                except Exception:
                    log.warning('Staged upload retrieval failed',exc_info=False)
                    raise error(503,'staged_upload_unavailable','Could not retrieve the uploaded files. Upload again.')
                # Reconstruct large form metadata before the original validators execute.
                metadata = [file for key,file in files if key == METADATA_FIELD]
                entries = list(form.multi_items())
                if metadata:
                    if len(metadata) != 1 or entries:
                        raise error(400,'invalid_staged_upload','Invalid staged form metadata.')
                    try:
                        entries = json.loads(await metadata[0].read())
                        if not isinstance(entries,list) or len(entries)>1000 or any(
                            not isinstance(pair,list) or len(pair)!=2 or not all(isinstance(x,str) for x in pair)
                            or pair[0] in FIELDS[feature] | {METADATA_FIELD} for pair in entries):
                            raise ValueError()
                        # Match the existing Next proxy duplicate-field protection.
                        for name in ('text','questions_json'):
                            if sum(key == name and bool(value.strip()) for key,value in entries)>1:
                                raise ValueError()
                        entries = [tuple(pair) for pair in entries]
                    except (ValueError, UnicodeError):
                        raise error(400,'invalid_staged_upload','Invalid staged form metadata.') from None
                # Starlette caches parsed form data here. FastAPI still runs ALL normal dependencies,
                # Pydantic form validation, feature policies, quarantine and malware scanning.
                request._form = FormData([*entries,*[(key,file) for key,file in files if key != METADATA_FIELD]])
                return await original(request)
            finally:
                await form.close()
                for _,file in files:
                    await file.close()
        return handler


def sweep():
    """Durable expiry cleanup, independent of browser completion and local disk."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT id,manifest FROM redocx_staged_uploads WHERE NOT cleaned AND cleanup_at<=now() ORDER BY cleanup_at LIMIT 100')
            rows=cur.fetchall()
    for row in rows:
        ticket,raw = row_values(row)
        try:
            for i in range(len(json.loads(raw))):
                delete_object(str(ticket),i)
            with get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("UPDATE redocx_staged_uploads SET cleaned=true, manifest='[]' WHERE id=%s",(str(ticket),))
                conn.commit()
        except Exception:
            log.warning('Staged upload cleanup failed; will retry')
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM redocx_staged_uploads WHERE cleaned AND created_at<now()-interval '2 hours'")
        conn.commit()


def start_cleanup():
    if not enabled():
        return None
    storage()  # Fail startup on missing configuration.
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT id FROM redocx_staged_uploads LIMIT 0')
            cur.execute('SELECT id FROM redocx_staged_downloads LIMIT 0')
    stop=threading.Event()
    def loop():
        while not stop.is_set():
            try:
                sweep()
            except Exception:
                log.warning('Staged upload sweep failed; will retry')
            try:
                sweep_downloads()
            except Exception:
                log.warning('Download cleanup failed; will retry')
            stop.wait(60)
    thread=threading.Thread(target=loop,name='railway-upload-cleanup',daemon=True)
    thread.start()
    return stop,thread


def stop_cleanup(worker):
    if worker:
        stop,thread=worker
        stop.set()
        thread.join(timeout=2)


def stage_download(handle, size, headers, content_type, owner):
    """Called only AFTER the original owner/token/integrity checks. No permanent storage."""
    if size <= THRESHOLD:
        return None
    # Single PutObject, no abandoned multipart sessions. The cap is configurable downward.
    if size > min(5_000_000_000, number('RAILWAY_DOWNLOAD_MAX_BYTES', 5_000_000_000)):
        raise error(413,'staged_download_size_exceeded','Download exceeds temporary delivery capacity.')
    ttl = min(900, number('RAILWAY_DOWNLOAD_URL_TTL_SECONDS', 300))
    ticket = str(uuid4())
    key = DOWNLOAD_PREFIX + ticket
    if not owner:
        raise error(503,'staged_download_unavailable','Download authorization is unavailable.')
    owner_digest = hashlib.sha256(str(owner).encode('utf-8')).hexdigest()
    try:
        client,bucket = storage()
        # Record before writing, so process crashes cannot orphan an untracked object.
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT pg_advisory_xact_lock(723019463)')
                cur.execute("""SELECT COALESCE(SUM(size_bytes),0) AS global_bytes, COUNT(*) AS global_count,
                    COALESCE(SUM(size_bytes) FILTER (WHERE owner_digest=%s),0) AS own_bytes, COUNT(*) FILTER (WHERE owner_digest=%s) AS own_count
                    FROM redocx_staged_downloads WHERE created_at>now()-interval '1 hour'""",(owner_digest,owner_digest))
                used,count,own_bytes,own_count=row_values(cur.fetchone())
                if used+size>number('RAILWAY_DOWNLOAD_GLOBAL_HOURLY_BYTES',100*1024*MB) or count>=number('RAILWAY_DOWNLOAD_GLOBAL_HOURLY_TICKETS',2000) or own_bytes+size>number('RAILWAY_DOWNLOAD_OWNER_HOURLY_BYTES',20*1024*MB) or own_count>=number('RAILWAY_DOWNLOAD_OWNER_HOURLY_TICKETS',120):
                    raise error(429,'staged_download_rate_limited','Temporary download capacity reached. Try again later.')
                cur.execute("INSERT INTO redocx_staged_downloads(id,size_bytes,owner_digest,cleanup_at) VALUES (%s,%s,%s,now()+interval '2 hours')",(ticket,size,owner_digest))
            conn.commit()
        # Never render arbitrary Vault/HTML/SVG content inline on the storage origin.
        safe_inline = {'application/pdf','image/jpeg','image/png','audio/mpeg','audio/mp4','audio/ogg','audio/webm','audio/wav','audio/aac','audio/flac','video/mp4','video/webm'}
        mime = str(content_type or 'application/octet-stream').split(';',1)[0].strip().lower()
        disposition = headers.get('content-disposition','attachment')
        if mime not in safe_inline:
            disposition = re.sub(r'^inline\b','attachment',disposition,flags=re.I)
            if mime not in {'application/zip','application/octet-stream'} and not re.fullmatch(r'[a-z0-9.+-]+/[a-z0-9.+-]+',mime):
                mime = 'application/octet-stream'
        handle.seek(0, os.SEEK_END)
        if handle.tell() != size:
            raise error(409,'staged_download_unavailable','Output changed before delivery. Please retry.')
        handle.seek(0)
        client.put_object(Bucket=bucket,Key=key,Body=handle,ContentLength=size,
            ContentType=mime,ContentDisposition=disposition,CacheControl='private, no-store, max-age=0')
        url = client.generate_presigned_url('get_object',Params={'Bucket':bucket,'Key':key},ExpiresIn=ttl)
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE redocx_staged_downloads SET cleanup_at=now()+(%s+900)*interval '1 second' WHERE id=%s",(ttl,ticket))
            conn.commit()
        return RedirectResponse(url,status_code=302,headers={'Cache-Control':'private, no-store, max-age=0','Referrer-Policy':'no-referrer','X-ReDOCX-Bucket-Delivery':'1'})
    except HTTPException:
        raise
    except Exception:
        log.warning('Temporary download staging failed')
        raise error(503,'staged_download_unavailable','Large download is temporarily unavailable. Please try again.') from None


def maybe_stage_file_response(response, owner):
    with open(response.path,'rb') as handle:
        size=os.fstat(handle.fileno()).st_size
        return stage_download(handle,size,response.headers,response.media_type,owner) or response


def sweep_downloads():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT id FROM redocx_staged_downloads WHERE NOT cleaned AND cleanup_at<=now() ORDER BY cleanup_at LIMIT 100')
            rows=cur.fetchall()
    if rows:
        client,bucket=storage()
        for row in rows:
            (ticket,) = row_values(row)
            try:
                client.delete_object(Bucket=bucket,Key=DOWNLOAD_PREFIX+str(ticket))
                with get_db() as conn:
                    with conn.cursor() as cur:
                        cur.execute('UPDATE redocx_staged_downloads SET cleaned=true WHERE id=%s',(str(ticket),))
                    conn.commit()
            except Exception:
                log.warning('Temporary download cleanup failed; will retry')
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM redocx_staged_downloads WHERE cleaned AND created_at<now()-interval '2 hours'")
        conn.commit()
