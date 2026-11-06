"""Launch the widget, the local API, or a credential-free design preview."""
from __future__ import annotations

import argparse
import sys
import threading


def main() -> int:
    parser = argparse.ArgumentParser(description="Codex 实时额度 · 本地 API 与玻璃桌面组件")
    parser.add_argument("--api-only", action="store_true", help="仅启动本地 API")
    parser.add_argument("--no-api", action="store_true", help="只显示组件，不启动 HTTP API")
    parser.add_argument("--port", type=int, default=18765, help="本地 API 端口，默认 18765")
    parser.add_argument("--auth-file", help="指定 Codex auth.json 路径")
    parser.add_argument("--demo", action="store_true", help="用示例数据预览设计，不读取凭据、不启动 API")
    parser.add_argument("--render-preview", metavar="PNG", help="将示例设计保存为图片后退出")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("端口必须介于 1 和 65535")
    if args.api_only and (args.demo or args.render_preview or args.no_api):
        parser.error("--api-only 不能与预览或 --no-api 同时使用")
    if args.api_only:
        from .backend import UsageService, start_api

        service = UsageService(auth_path=args.auth_file)
        try:
            server, _ = start_api(service, port=args.port)
        except OSError:
            print(f"无法监听 127.0.0.1:{args.port}，请检查端口是否已占用。", file=sys.stderr)
            return 1
        print(f"Codex 额度 API：http://127.0.0.1:{args.port}/api/usage")
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            pass
        finally:
            server.shutdown()
            server.server_close()
        return 0

    from .widget import run_widget

    return run_widget(args)


if __name__ == "__main__":
    raise SystemExit(main())
