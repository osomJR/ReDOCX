"""Split-only admission limits, checked before materializing output page lists."""

MAX_SPLIT_OUTPUT_FILES = 200
MAX_SPLIT_PAGE_COPIES = 2_000


def validate_split_workload(*, mode, page_count=None, selected_pages=(), page_ranges=()):
    mode = getattr(mode, "value", mode)
    if mode == "every_page":
        if page_count is None:
            return
        output_count = copy_count = page_count
    elif mode == "extract_selected_pages":
        output_count, copy_count = 1, len(selected_pages)
    elif mode == "page_ranges":
        intervals = [(item.start_page, item.end_page) for item in page_ranges]
        if len(set(intervals)) != len(intervals):
            raise ValueError("page_ranges cannot contain duplicate ranges.")
        # Partial overlaps are allowed: each interval represents a separate PDF.
        output_count = len(intervals)
        copy_count = sum(end - start + 1 for start, end in intervals)
    else:
        raise ValueError(f"Unsupported split mode: {mode}")
    if output_count > MAX_SPLIT_OUTPUT_FILES:
        raise ValueError(f"Split PDF supports at most {MAX_SPLIT_OUTPUT_FILES} output files per request.")
    if copy_count > MAX_SPLIT_PAGE_COPIES:
        raise ValueError(f"Split PDF supports at most {MAX_SPLIT_PAGE_COPIES} copied pages per request, including overlaps.")
