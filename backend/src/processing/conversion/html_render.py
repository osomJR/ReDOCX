"""Print static HTML with Chromium and a bounded, DNS-pinned asset broker.

Chromium has no direct HTTP/file access: all subresources go through the broker.
The renderer runs without application secrets, under a dedicated OS user when
the API runs as root. Document scripts, workers, frames and downloads are off.
"""
from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit
import gzip
import http.client
import ipaddress
import json
import mimetypes
import os
import pwd
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import time

from bs4 import BeautifulSoup


class AssetError(ValueError):
    pass


@dataclass(frozen=True)
class Asset:
    body: bytes
    content_type: str


class AssetBroker:
    def __init__(self, *, root=None, allowed_hosts=(), max_asset=10*1024*1024,
                 max_total=40*1024*1024, max_requests=96, timeout=10,
                 resolver=socket.getaddrinfo, transport=None):
        self.root = Path(root).resolve() if root else None
        self.allowed_hosts = {str(x).casefold().rstrip('.') for x in allowed_hosts}
        self.max_asset, self.max_total = max_asset, max_total
        self.max_requests, self.timeout = max_requests, timeout
        self.resolver, self.transport = resolver, transport or self._request
        self.used = self.requests = 0
        self.deadline = time.monotonic()+60
        self.cache = {}

    def _address(self, url):
        parsed = urlsplit(url)
        if parsed.scheme not in ('http', 'https') or parsed.username or parsed.password:
            raise AssetError('Only credential-free HTTP(S) asset URLs are supported.')
        host = (parsed.hostname or '').encode('idna').decode('ascii').casefold().rstrip('.')
        if not host or '%' in host or len(host)>253:
            raise AssetError('Invalid asset hostname.')
        try:
            port = parsed.port or (443 if parsed.scheme=='https' else 80)
        except ValueError as exc:
            raise AssetError('Invalid asset port.') from exc
        if port != (443 if parsed.scheme=='https' else 80):
            raise AssetError('Nonstandard asset ports are disabled.')
        if self.allowed_hosts and host not in self.allowed_hosts:
            raise AssetError('Asset hostname is outside the configured allowlist.')
        try:
            records = self.resolver(host, port, type=socket.SOCK_STREAM)
        except OSError as exc:
            raise AssetError('Asset hostname could not be resolved.') from exc
        addresses = []
        for record in records:
            ip = ipaddress.ip_address(record[4][0])
            effective = ip.ipv4_mapped if isinstance(ip,ipaddress.IPv6Address) and ip.ipv4_mapped else ip
            if (not effective.is_global or effective.is_multicast or effective.is_reserved
                    or effective.is_loopback or effective.is_link_local):
                raise AssetError('Private, local, reserved and metadata asset addresses are blocked.')
            addresses.append((record[0],str(ip)))
        if not addresses:
            raise AssetError('Asset hostname has no usable public address.')
        addresses.sort(key=lambda x:x[0]!=socket.AF_INET)
        return parsed,host,port,addresses[0][1]

    def _request(self, parsed, host, port, address):
        remaining = min(self.timeout,self.deadline-time.monotonic())
        if remaining<=0:
            raise AssetError('HTML asset deadline exceeded.')
        # Connect to the validated address, not a second hostname lookup. HTTPS
        # still verifies the original hostname and uses it for SNI and Host.
        connection = http.client.HTTPConnection(host,port,timeout=remaining)
        sock = socket.create_connection((address,port),timeout=remaining)
        if parsed.scheme=='https':
            sock = ssl.create_default_context().wrap_socket(sock,server_hostname=host)
        connection.sock = sock
        path = parsed.path or '/'
        if parsed.query:
            path += '?'+parsed.query
        try:
            connection.request('GET',path,headers={'Host':host,'User-Agent':'ReDOCX-AssetBroker/2',
                                                  'Accept-Encoding':'identity','Connection':'close'})
            response = connection.getresponse()
            headers = {k.casefold():v for k,v in response.getheaders()}
            if response.status in (301,302,303,307,308):
                return response.status,headers,b''
            length = headers.get('content-length','')
            if length.isdigit() and int(length)>self.max_asset:
                raise AssetError('HTML asset exceeds the size limit.')
            body = response.read(self.max_asset+1)
            if len(body)>self.max_asset:
                raise AssetError('HTML asset exceeds the size limit.')
            encoding = headers.get('content-encoding','identity').casefold()
            if encoding=='gzip':
                with gzip.GzipFile(fileobj=BytesIO(body)) as stream:
                    body = stream.read(self.max_asset+1)
            elif encoding not in ('identity',''):
                raise AssetError('Unsupported asset content encoding.')
            return response.status,headers,body
        finally:
            connection.close()

    def fetch(self,url):
        if url in self.cache:
            return self.cache[url]
        original = url
        self.requests += 1
        if self.requests>self.max_requests or time.monotonic()>self.deadline:
            raise AssetError('HTML asset request budget exceeded.')
        for hop in range(4):
            parsed = urlsplit(url)
            if parsed.hostname=='redocx-assets.invalid':
                if not self.root or parsed.scheme!='https' or parsed.query:
                    raise AssetError('Relative assets need an approved HTML asset root or base URL.')
                name = unquote(parsed.path).lstrip('/')
                if '\\' in name or '\x00' in name:
                    raise AssetError('Invalid relative asset path.')
                target = (self.root/name).resolve()
                if not target.is_relative_to(self.root) or not target.is_file():
                    raise AssetError('Relative asset is missing or outside the approved asset root.')
                if target.stat().st_size>self.max_asset:
                    raise AssetError('HTML asset exceeds the size limit.')
                body = target.read_bytes()
                content_type = mimetypes.guess_type(target.name)[0] or 'application/octet-stream'
                break
            parsed,host,port,address = self._address(url)
            status,headers,body = self.transport(parsed,host,port,address)
            if status in (301,302,303,307,308):
                if hop==3 or not headers.get('location'):
                    raise AssetError('HTML asset redirect limit exceeded.')
                url = urljoin(url,headers['location'])
                continue
            if status!=200:
                raise AssetError('An HTML asset could not be downloaded successfully.')
            content_type = headers.get('content-type','application/octet-stream').split(';',1)[0]
            break
        if len(body)>self.max_asset or self.used+len(body)>self.max_total:
            raise AssetError('HTML asset byte budget exceeded.')
        self.used += len(body)
        asset = Asset(body,content_type)
        self.cache[original] = asset
        return asset


def render_html(text, *, asset_root=None, base_url=None, hosts=(), sandbox=True):
    from playwright.sync_api import sync_playwright
    if len(text.encode('utf-8'))>25*1024*1024:
        raise ValueError('HTML document exceeds the rendering size limit.')
    soup = BeautifulSoup(text,'html.parser')
    if soup.head is None:
        head = soup.new_tag('head')
        if soup.html:
            soup.html.insert(0,head)
        else:
            soup.insert(0,head)
    existing = soup.find('base',href=True)
    base = base_url or (existing['href'] if existing else None) or 'https://redocx-assets.invalid/'
    if urlsplit(base).scheme not in ('http','https'):
        raise AssetError('HTML base URL must use HTTP(S).')
    for tag in soup.find_all('base'):
        tag.decompose()
    tag = soup.new_tag('base',href=base)
    soup.head.insert(0,tag)
    broker = AssetBroker(root=asset_root,allowed_hosts=hosts)
    failures = []
    with sync_playwright() as runtime:
        browser = runtime.chromium.launch(headless=True,chromium_sandbox=sandbox)
        try:
            context = browser.new_context(java_script_enabled=False,service_workers='block',
                accept_downloads=False,viewport={'width':794,'height':1123},locale='en-GB',
                reduced_motion='reduce')
            def intercept(route):
                try:
                    if route.request.resource_type not in ('image','font','stylesheet'):
                        raise AssetError('HTML scripts, frames and active network requests are blocked.')
                    asset = broker.fetch(route.request.url)
                    route.fulfill(status=200,body=asset.body,content_type=asset.content_type,
                        headers={'Access-Control-Allow-Origin':'*','Cache-Control':'no-store'})
                except Exception as exc:
                    failures.append(type(exc).__name__+': '+str(exc))
                    route.abort('blockedbyclient')
            context.route('**/*',intercept)
            page = context.new_page()
            page.set_default_timeout(30000)
            page.emulate_media(media='print')
            page.set_content(str(soup),wait_until='networkidle',timeout=60000)
            page.evaluate('() => document.fonts.ready.then(() => true)')
            if failures:
                raise AssetError('HTML asset loading failed: '+failures[0])
            # CSS owns pagination. A4 with zero default margins reproduces normal
            # browser print geometry; explicit @page sizes and breaks take priority.
            result = page.pdf(format='A4',prefer_css_page_size=True,print_background=True,
                margin={key:'0' for key in ('top','right','bottom','left')},tagged=True,
                display_header_footer=False)
            if not result.startswith(b'%PDF-') or len(result)>100*1024*1024:
                raise RuntimeError('Chromium did not produce an acceptable PDF.')
            return result
        finally:
            browser.close()


def convert(source: Path, output: Path):
    text = source.read_text(encoding='utf-8')
    sandbox = os.getenv('REDOCX_HTML_BROWSER_SANDBOX','1')!='0'
    user = os.getenv('REDOCX_HTML_RENDER_USER','redocx-renderer')
    command = [sys.executable,'-B',str(Path(__file__).resolve()),'--worker']
    identity = None
    if os.name=='posix' and os.geteuid()==0:
        try:
            identity = pwd.getpwnam(user)
        except KeyError:
            if sandbox:
                raise RuntimeError('The dedicated HTML renderer user is missing; rebuild the backend image.')
        if identity:
            runuser = shutil.which('runuser')
            if not runuser:
                raise RuntimeError('The HTML renderer privilege-drop utility is missing.')
            command = [runuser,'-u',user,'--']+command
    settings = {'text':text,'asset_root':os.getenv('REDOCX_HTML_ASSET_ROOT') or None,
                'base_url':os.getenv('REDOCX_HTML_BASE_URL') or None,
                'hosts':[x.strip() for x in os.getenv('REDOCX_HTML_ASSET_HOSTS','').split(',') if x.strip()],
                'sandbox':sandbox}
    with tempfile.TemporaryDirectory(prefix='html-renderer-') as home:
        if identity:
            os.chown(home,identity.pw_uid,identity.pw_gid)
        environment = {k:v for k,v in os.environ.items() if k in {
            'PATH','PYTHONPATH','PLAYWRIGHT_BROWSERS_PATH','FONTCONFIG_PATH','FONTCONFIG_FILE',
            'XDG_DATA_DIRS','LD_LIBRARY_PATH'}}
        environment.update({'HOME':home,'TMPDIR':home,'LANG':'C.UTF-8','LC_ALL':'C.UTF-8',
                            'PYTHONDONTWRITEBYTECODE':'1'})
        try:
            result = subprocess.run(command,input=json.dumps(settings).encode('utf-8'),
                capture_output=True,env=environment,timeout=100,check=False)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError('HTML rendering exceeded its time limit.') from exc
        if result.returncode or not result.stdout.startswith(b'%PDF-'):
            detail = result.stderr.decode('utf-8','replace').strip()[-1200:]
            raise RuntimeError('HTML rendering failed. '+detail)
        output.write_bytes(result.stdout)


if __name__=='__main__':
    try:
        payload = json.loads(sys.stdin.buffer.read(30*1024*1024))
        sys.stdout.buffer.write(render_html(**payload))
    except Exception as exc:
        print(type(exc).__name__+': '+str(exc),file=sys.stderr)
        raise SystemExit(1)
