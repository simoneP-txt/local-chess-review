"""Measurement server: serves spike/ on 127.0.0.1 only, cross-origin isolated, and stores runs.

    python spike/bench/server.py [--port 8000]

POST /result takes a RawRun with any Content-Type (the browser sends text/plain and skips the
preflight) and writes spike/results/<label>.json. Standard library only: S2 runs it without the venv.
"""
import argparse
import json
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import uci


class Handler(SimpleHTTPRequestHandler):
    # Explicit types: the Windows registry can map .js to text/plain, which workers refuse.
    extensions_map = {**SimpleHTTPRequestHandler.extensions_map,
                      ".js": "text/javascript", ".wasm": "application/wasm"}

    def end_headers(self):
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Cross-Origin-Embedder-Policy", "require-corp")
        self.send_header("Access-Control-Allow-Origin", "*")
        super().end_headers()

    def do_POST(self):
        if self.path != "/result":
            return self.send_error(404)
        try:
            raw = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            path = uci.result_path(raw["label"])
            result = uci.parse_run(raw)
            uci.save_result(path, result)
        except FileExistsError as e:
            return self._reply(409, {"error": str(e)})
        except Exception as e:  # anything malformed: say why and write nothing
            return self._reply(400, {"error": f"{type(e).__name__}: {e}"})
        self._reply(201, {key: value for key, value in result.items() if key != "positions"})

    def _reply(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Serves spike/ and stores the runs sent to POST /result.")
    parser.add_argument("--port", type=int, default=8000)
    port = parser.parse_args(argv).port
    server = ThreadingHTTPServer(("127.0.0.1", port), partial(Handler, directory=str(uci.SPIKE)))
    print(f"serving spike/ on http://127.0.0.1:{port}/bench/web/index.html", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
