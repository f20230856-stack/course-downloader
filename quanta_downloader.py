import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Dict, List, Tuple
from urllib.parse import urljoin, urlparse

import requests
import urllib3
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


def sanitize_name(name: str) -> str:
    name = re.sub(r"[\\/:*?\"<>|]", "_", name)
    return re.sub(r"\s+", " ", name).strip()


def clean_course_name(name: str) -> str:
    cleaned = sanitize_name(name)

    # Moodle dashboard cards may include hidden accessibility text in title blocks.
    noise_patterns = [
        r"(?i)^course is starred\.?\s*",
        r"(?i)^course is not starred\.?\s*",
        r"(?i)^star this course\.?\s*",
        r"(?i)^unstar this course\.?\s*",
    ]
    for pattern in noise_patterns:
        cleaned = re.sub(pattern, "", cleaned)

    cleaned = re.sub(r"(?i)^course name\s+", "", cleaned)
    cleaned = re.sub(r"(?i)^course\s+", "", cleaned)

    return sanitize_name(cleaned)


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path

    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    counter = 1

    while True:
        candidate = parent / f"{stem} ({counter}){suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def ensure_absolute_url(base_url: str, maybe_relative: str) -> str:
    return urljoin(base_url, maybe_relative)


def to_cookiejar(cookies: List[Dict]) -> requests.cookies.RequestsCookieJar:
    jar = requests.cookies.RequestsCookieJar()
    for cookie in cookies:
        jar.set(
            cookie.get("name", ""),
            cookie.get("value", ""),
            domain=cookie.get("domain", ""),
            path=cookie.get("path", "/"),
        )
    return jar


def parse_courses(page, config: Dict) -> List[Tuple[str, str]]:
    selectors = config["selectors"]
    base_url = config["base_url"]

    course_selector = selectors["course_card"]
    course_name_selector = selectors["course_name"]
    course_link_selector = selectors["course_link"]

    courses = page.eval_on_selector_all(
        course_selector,
        """
        (cards, selectors) => cards.map(card => {
            const nameEl = card.querySelector(selectors.nameSel);
            const linkEl = card.querySelector(selectors.linkSel);
            return {
                name: nameEl ? nameEl.textContent.trim() : '',
                link: linkEl ? linkEl.getAttribute('href') : ''
            };
        })
        """,
        {"nameSel": course_name_selector, "linkSel": course_link_selector},
    )

    normalized = []
    for item in courses:
        name = clean_course_name(item.get("name", ""))
        link = item.get("link", "")
        if not name or not link:
            continue
        normalized.append((name, ensure_absolute_url(base_url, link)))

    return normalized


def parse_files(page, config: Dict) -> List[Tuple[str, str]]:
    selectors = config["selectors"]
    base_url = config["base_url"]

    rows_selector = selectors["file_row"]
    file_name_selector = selectors["file_name"]
    file_link_selector = selectors["file_link"]

    files = page.eval_on_selector_all(
        rows_selector,
        """
        (rows, selectors) => rows.map(row => {
            const nameEl = row.querySelector(selectors.nameSel);
            const linkEl = row.querySelector(selectors.linkSel);
            return {
                name: nameEl ? nameEl.textContent.trim() : '',
                link: linkEl ? linkEl.getAttribute('href') : ''
            };
        })
        """,
        {"nameSel": file_name_selector, "linkSel": file_link_selector},
    )

    normalized = []
    for item in files:
        name = sanitize_name(item.get("name", ""))
        link = item.get("link", "")
        if not name or not link:
            continue
        abs_link = ensure_absolute_url(base_url, link)
        normalized.append((name, abs_link))

    return normalized


def parse_files_fallback(page, config: Dict) -> List[Tuple[str, str]]:
    base_url = config["base_url"]

    links = page.eval_on_selector_all(
        "a[href]",
        """
        (anchors) => anchors.map(a => {
            const href = a.getAttribute('href') || '';
            const text = (a.textContent || '').trim();
            return { href, text };
        })
        """,
    )

    seen = set()
    normalized: List[Tuple[str, str]] = []

    for item in links:
        href = (item.get("href") or "").strip()
        text = sanitize_name((item.get("text") or "").strip())
        if not href:
            continue

        abs_link = ensure_absolute_url(base_url, href)
        url_l = abs_link.lower()
        text_l = text.lower()

        is_candidate = (
            ".pdf" in url_l
            or ".pdf" in text_l
            or "/mod/resource/view.php" in url_l
            or "/pluginfile.php/" in url_l
        )
        if not is_candidate:
            continue

        file_name = text if text else Path(urlparse(abs_link).path).name
        if not file_name:
            file_name = "document.pdf"

        key = (file_name.lower(), abs_link)
        if key in seen:
            continue
        seen.add(key)
        normalized.append((sanitize_name(file_name), abs_link))

    return normalized


def should_download_pdf(file_name: str, file_url: str) -> bool:
    combined = f"{file_name} {file_url}".lower()
    return (
        ".pdf" in combined
        or "/mod/resource/view.php" in file_url.lower()
        or "/pluginfile.php/" in file_url.lower()
    )


def infer_extension(file_name: str, file_url: str) -> str:
    name_suffix = Path(file_name).suffix
    if name_suffix:
        return name_suffix

    parsed = urlparse(file_url)
    url_suffix = Path(parsed.path).suffix
    if url_suffix and url_suffix.lower() != ".php":
        return url_suffix

    return ".pdf"


def download_file(
    session: requests.Session,
    file_url: str,
    destination: Path,
    timeout_seconds: int,
    referer: str,
    verify_ssl: bool,
) -> None:
    headers = {"Referer": referer}
    with session.get(file_url, stream=True, timeout=timeout_seconds, headers=headers, verify=verify_ssl) as response:
        response.raise_for_status()
        with open(destination, "wb") as out_file:
            for chunk in response.iter_content(chunk_size=1024 * 64):
                if chunk:
                    out_file.write(chunk)


def load_config(config_path: Path) -> Dict:
    with open(config_path, "r", encoding="utf-8") as file:
        return json.load(file)


def run(config_path: Path) -> int:
    config = load_config(config_path)

    required_top_keys = ["base_url", "login_url", "courses_page_url", "semester_name", "download_root", "selectors"]
    missing = [k for k in required_top_keys if k not in config]
    if missing:
        print(f"Missing required config keys: {', '.join(missing)}")
        return 1

    download_root = Path(config["download_root"]).resolve()
    semester_folder = download_root / sanitize_name(config["semester_name"])
    semester_folder.mkdir(parents=True, exist_ok=True)

    timeout_seconds = int(config.get("request_timeout_seconds", 30))
    verify_ssl = bool(config.get("verify_ssl", True))
    headless = bool(config.get("headless", False))

    if not verify_ssl:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        print("SSL verification disabled for file downloads (verify_ssl=false).")

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=headless)
        except Exception as ex:
            message = str(ex)
            print(f"Failed to launch browser: {message}")
            if "Executable doesn't exist" in message:
                print("Run this once to install browser runtime:")
                print("  .\\.venv\\Scripts\\python.exe -m playwright install chromium")
            if "spawn EFTYPE" in message and headless:
                print("Headless launch is not supported in this environment.")
                print("Set \"headless\": false in your config and run again.")
            return 1

        context = browser.new_context(accept_downloads=False)
        page = context.new_page()

        print(f"Opening login page: {config['login_url']}")
        page.goto(config["login_url"], wait_until="networkidle")
        print("Please complete login in the browser window.")

        login_ready_selector = config["selectors"].get("login_ready")
        if not login_ready_selector:
            print("Missing selector: selectors.login_ready")
            browser.close()
            return 1

        try:
            page.wait_for_selector(login_ready_selector, timeout=10 * 60 * 1000)
        except PlaywrightTimeoutError:
            print("Timed out waiting for login completion selector.")
            browser.close()
            return 1

        print("Login detected.")
        print(f"Opening courses page: {config['courses_page_url']}")
        page.goto(config["courses_page_url"], wait_until="networkidle")

        try:
            page.wait_for_selector(config["selectors"]["course_card"], timeout=45_000)
        except PlaywrightTimeoutError:
            print("Could not find course cards. Check selectors.course_card.")
            browser.close()
            return 1

        courses = parse_courses(page, config)
        if not courses:
            print("No courses found. Check course selectors.")
            browser.close()
            return 1

        print(f"Found {len(courses)} course(s).")

        session = requests.Session()
        session.cookies = to_cookiejar(context.cookies())
        user_agent = page.evaluate("() => navigator.userAgent")
        session.headers.update({"User-Agent": user_agent})

        downloaded_count = 0
        skipped_existing = 0

        for idx, (course_name, course_url) in enumerate(courses, start=1):
            print(f"[{idx}/{len(courses)}] Course: {course_name}")
            subject_dir = semester_folder / sanitize_name(course_name)
            subject_dir.mkdir(parents=True, exist_ok=True)

            page.goto(course_url, wait_until="networkidle")

            files_tab_selector = config["selectors"].get("files_tab")
            if files_tab_selector:
                locator = page.locator(files_tab_selector)
                if locator.count() > 0:
                    locator.first.click()
                    page.wait_for_load_state("networkidle")
                    time.sleep(1)

            files: List[Tuple[str, str]] = []
            file_row_selector = config["selectors"].get("file_row", "")
            if file_row_selector:
                try:
                    page.wait_for_selector(file_row_selector, timeout=10_000)
                    files = parse_files(page, config)
                except PlaywrightTimeoutError:
                    files = []

            if not files:
                files = parse_files_fallback(page, config)

            if not files:
                print(f"  No downloadable links found for course: {course_name}")
                continue

            print(f"  Candidate file links found: {len(files)}")

            for file_name, file_url in files:
                if not should_download_pdf(file_name, file_url):
                    continue

                ext = infer_extension(file_name, file_url)
                final_name = file_name if Path(file_name).suffix else f"{file_name}{ext}"
                target_path = subject_dir / sanitize_name(final_name)

                if target_path.exists():
                    skipped_existing += 1
                    continue

                target_path = unique_path(target_path)

                try:
                    download_file(
                        session=session,
                        file_url=file_url,
                        destination=target_path,
                        timeout_seconds=timeout_seconds,
                        referer=course_url,
                        verify_ssl=verify_ssl,
                    )
                    downloaded_count += 1
                    print(f"  Downloaded: {target_path.name}")
                except requests.RequestException as ex:
                    print(f"  Failed download: {file_name} -> {ex}")

        browser.close()

    print("Run completed.")
    print(f"Downloaded files: {downloaded_count}")
    print(f"Skipped existing files: {skipped_existing}")
    print(f"Output folder: {semester_folder}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Download and organize Quanta course files by semester and subject.")
    parser.add_argument(
        "--config",
        required=True,
        help="Path to JSON configuration file.",
    )
    args = parser.parse_args()

    config_path = Path(args.config)
    if not config_path.exists():
        print(f"Config file not found: {config_path}")
        sys.exit(1)

    exit_code = run(config_path)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
