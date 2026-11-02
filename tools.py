"""Laptop commands the phone can run, as explicit slash commands.

Only the functions in COMMANDS can run - the phone can never run arbitrary
programs on the laptop. Each respects Settings → Access (allowed folders,
blocked secrets, the screen-capture and file-search switches), and a file
can only be downloaded through a link the server itself signed.

A command is `fn(args, ctx) -> str` returning Markdown; ctx gives:
    ctx.link(path)        signed download link
    ctx.ask_link(path)    signed "ask about this file" link
    ctx.gate, ctx.settings, ctx.shots_dir
"""
import html
import json
import os
import re
import time
import urllib.parse
import urllib.request

FIND_LIMIT = 15
FIND_SECONDS = 8
SKIP_DIRS = {"node_modules", ".git", "venv", ".venv", "__pycache__", "AppData", "site-packages",
             "$RECYCLE.BIN", ".cache", ".idea", ".vscode"}


def _size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def cmd_help(args, ctx):
    return ("**Laptop commands**\n"
            "- `/find dsa notes` — search the allowed folders by file name; tap a result to download it "
            "or ask about it\n"
            "- `/screenshot` — see the laptop's screen right now\n"
            "- `/web best budget laptop` — search the web from the laptop\n"
            "- `/status` — is everything on the laptop running?\n"
            "- `/brief` — today's classes, deadlines and open items · `/reminders` — what's coming up\n"
            "- `remind me … Friday 5pm` — set a reminder in plain words\n\n"
            "Anything without a `/` goes to the AI, with your memory, like on the laptop.")


def cmd_find(args, ctx):
    if not ctx.settings.access.get("file_search", True):
        return "File search is turned off on the laptop (Settings → Access)."
    words = [w.lower() for w in args.split()]
    if not words:
        return "Tell me what to look for, e.g. `/find dsa notes`"
    roots = [d for d in ctx.settings.access.get("folders", []) if os.path.isdir(d)]
    if not roots:
        return "No folders are allowed on the laptop yet (Settings → Access)."
    found, start = [], time.time()
    for root in roots:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
            for name in filenames:
                low = name.lower()
                if all(w in low for w in words):
                    path = os.path.join(dirpath, name)
                    if ctx.gate.blocked_reason(os.path.normcase(os.path.realpath(path))):
                        continue
                    try:
                        st = os.stat(path)
                    except OSError:
                        continue
                    found.append((st.st_mtime, st.st_size, path))
            if time.time() - start > FIND_SECONDS:
                break
        if time.time() - start > FIND_SECONDS:
            break
    if not found:
        return f"No files matching **{args}** in {', '.join(os.path.basename(r) for r in roots)}."
    found.sort(reverse=True)
    home = os.path.expanduser("~")
    lines = [f"Found {len(found)} file{'s' if len(found) != 1 else ''} matching **{args}**"
             + (f" (newest {FIND_LIMIT} shown)" if len(found) > FIND_LIMIT else "") + ":"]
    for mtime, size, path in found[:FIND_LIMIT]:
        where = os.path.dirname(path)
        where = "~" + where[len(home):] if where.startswith(home) else where
        when = time.strftime("%d %b %Y", time.localtime(mtime))
        lines.append(f"- [{os.path.basename(path)}]({ctx.link(path)}) · {_size(size)} · {when} · "
                     f"[ask about it]({ctx.ask_link(path)})  \n  `{where}`")
    return "\n".join(lines)


def cmd_screenshot(args, ctx):
    if not ctx.settings.access.get("screen_capture", True):
        return "Screen capture is turned off on the laptop (Settings → Access)."
    try:
        from PIL import ImageGrab
    except ImportError:
        return "Screenshots need Pillow on the laptop."
    img = ImageGrab.grab(all_screens=True).convert("RGB")
    img.thumbnail((2400, 2400))
    os.makedirs(ctx.shots_dir, exist_ok=True)
    for old in sorted(os.listdir(ctx.shots_dir))[:-10]:  # keep only the latest 10
        try:
            os.remove(os.path.join(ctx.shots_dir, old))
        except OSError:
            pass
    path = os.path.join(ctx.shots_dir, time.strftime("screen_%Y%m%d_%H%M%S.jpg"))
    img.save(path, quality=80)
    return f"Laptop screen at {time.strftime('%H:%M:%S')}:\n\n![screenshot]({ctx.link(path)})"


def cmd_web(args, ctx):
    if not args.strip():
        return "What should I search for? e.g. `/web python asyncio tutorial`"
    url = "https://html.duckduckgo.com/html/?" + urllib.parse.urlencode({"q": args})
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            page = r.read().decode("utf-8", "replace")
    except Exception as e:
        return f"Web search failed: {e}"
    return format_web(args, page)


def format_web(query, page):
    results = []
    for m in re.finditer(r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>(.*?)'
                         r'(?=<a[^>]*class="result__a"|$)', page, re.S):
        href, title, rest = m.groups()
        if "uddg=" in href:
            href = urllib.parse.parse_qs(urllib.parse.urlparse(href).query).get("uddg", [href])[0]
        if href.startswith("//"):
            href = "https:" + href
        if "duckduckgo.com/y.js" in href:  # ads
            continue
        snip = re.search(r'class="result__snippet"[^>]*>(.*?)</a>', rest, re.S)
        clean = lambda t: html.unescape(re.sub(r"<[^>]+>", "", t)).strip()  # noqa: E731
        results.append((clean(title), href, clean(snip.group(1)) if snip else ""))
        if len(results) == 6:
            break
    if not results:
        return "No web results (the search page may have changed or blocked the request)."
    lines = [f"Web results for **{query}**:"]
    for title, href, snip in results:
        lines.append(f"- [{title}]({href})" + (f"  \n  {snip}" if snip else ""))
    return "\n".join(lines)


def cmd_status(args, ctx):
    lines = ["**Laptop status**"]
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open("http://localhost:11434/api/tags", timeout=2) as r:
            names = {m["name"] for m in json.load(r).get("models", [])}
        lines.append(f"- Ollama: online · {len(names)} models installed")
    except Exception:
        lines.append("- Ollama: **offline** (local models won't answer)")
    for name, var in (("Gemini", "GEMINI_API_KEY"), ("Groq", "GROQ_API_KEY")):
        lines.append(f"- {name}: {'key set' if os.getenv(var) else '**no key**'}")
    acc = ctx.settings.access
    lines.append(f"- Allowed folders: {len(acc.get('folders', []))} · file search "
                 f"{'on' if acc.get('file_search', True) else 'off'} · screen capture "
                 f"{'on' if acc.get('screen_capture', True) else 'off'}")
    return "\n".join(lines)


def cmd_brief(args, ctx):
    host = getattr(ctx, "host", None)
    if not host or not getattr(host, "rem", None):
        return "Reminders aren't available on the laptop."
    return host.brief_text()


def cmd_reminders(args, ctx):
    import reminders
    rem = getattr(getattr(ctx, "host", None), "rem", None)
    if not rem:
        return "Reminders aren't available on the laptop."
    items = rem.upcoming()
    if not items:
        return "No reminders. Set one with `remind me … Friday 5pm`."
    return "**Coming up**\n" + "\n".join(f"- {reminders.describe(r['due_dt'], r['repeat'])}: {r['text']}"
                                          for r in items[:15])


COMMANDS = {"help": cmd_help, "brief": cmd_brief, "reminders": cmd_reminders, "find": cmd_find, "screenshot": cmd_screenshot, "web": cmd_web,
            "status": cmd_status}


def run(text, ctx):
    name, _, args = text[1:].strip().partition(" ")
    fn = COMMANDS.get(name.lower())
    if not fn:
        return f"Unknown command `/{name}`.\n\n" + cmd_help("", ctx)
    try:
        return fn(args.strip(), ctx)
    except Exception as e:
        return f"`/{name}` failed: {type(e).__name__}: {e}"
