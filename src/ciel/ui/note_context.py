"""A note can carry the place where its thought began.

**Only when asked.** The note window remembers which app yielded focus, but
reads a browser page only when the owner presses Context. Fixed AppleScripts
read an active page's title and URL, never page content. Other apps contribute
their name alone. No script contains a title or other untrusted argument.

**Visible before Save.** The app, quoted page title, and URL enter the draft,
where the owner can edit or remove it. Failure to read a page leaves an honest
app-only attachment rather than guessing a URL or inspecting another app.
"""
from __future__ import annotations

import json
import subprocess
from urllib.parse import urlsplit, urlunsplit


_BROWSERS = {
    'com.apple.Safari': ('name of current tab of front window', 'URL of current tab of front window'),
    'com.google.Chrome': ('title of active tab of front window', 'URL of active tab of front window'),
    'com.microsoft.edgemac': ('title of active tab of front window', 'URL of active tab of front window'),
    'com.brave.Browser': ('title of active tab of front window', 'URL of active tab of front window'),
    'org.chromium.Chromium': ('title of active tab of front window', 'URL of active tab of front window'),
    'company.thebrowser.Browser': ('title of active tab of front window', 'URL of active tab of front window'),
}


def capture_context(app_name: str, bundle_id: str) -> tuple[str, bool]:
    context = {'app': app_name[:200] or 'Unknown app'}
    expressions = _BROWSERS.get(bundle_id)
    page_missing = False
    if expressions:
        title, url = expressions
        script = f'tell application id "{bundle_id}"\nreturn ({title}) & linefeed & ({url})\nend tell'
        try:
            result = subprocess.run(['/usr/bin/osascript', '-e', script], capture_output=True,
                                    text=True, timeout=4, check=True)
            title, separator, url = result.stdout.strip().rpartition('\n')
            parts = urlsplit(url)
            if not separator or parts.scheme not in ('http', 'https') or not parts.hostname:
                raise ValueError('No web page')
            # URL credentials are never useful context for a passing thought.
            host = parts.netloc.rsplit('@', 1)[-1]
            context.update(page=title[:500], url=urlunsplit((parts.scheme, host, parts.path, parts.query, parts.fragment))[:3000])
        except (OSError, subprocess.SubprocessError, ValueError):
            page_missing = True
    lines = ['From ' + ' '.join(context['app'].split())]
    if 'page' in context:
        lines.extend([json.dumps(context['page'], ensure_ascii=False), context['url']])
    return '\n\n' + '\n'.join(lines), page_missing
