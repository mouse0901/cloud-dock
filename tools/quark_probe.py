"""Anonymous Quark protocol smoke check; never saves QR/session credentials."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx
from app.providers.quark_web import QuarkWebApi, QuarkApiError, SHARE, PARAMS


async def main():
    events = []
    checks = []
    async with httpx.AsyncClient(follow_redirects=False) as client:
        api = QuarkWebApi(client, diagnostic=events.append)
        qr = await api.create_qr()
        checks.append({"stage": "qr", "token_present": bool(qr.token), "official_payload": qr.url.startswith("https://su.quark.cn/4_eMHBJ?")})
        state = await api.poll(qr.token)
        checks.append({"stage": "poll", "state": state.state})
        try:
            await api._fetch_files("/", 0)
            checks.append({"stage": "directory", "unexpected_anonymous_access": True})
        except QuarkApiError as error:
            checks.append({"stage": "directory", "error_code": error.code})
        try:
            await api._request("POST", SHARE + "/share/sharepage/token", "missing_share", params=PARAMS,
                data={"pwd_id": "000000000000", "passcode": "", "support_visit_limit_private_share": True})
            checks.append({"stage": "missing_share", "unexpected_success": True})
        except QuarkApiError as error:
            checks.append({"stage": "missing_share", "error_code": error.code})
        api.reset()
    output = ROOT / "work" / "research" / "quark" / "adapter-anonymous-probe.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"at": datetime.now().astimezone().isoformat(), "checks": checks, "events": events}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"checks": checks, "diagnostic_events": len(events)}, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
