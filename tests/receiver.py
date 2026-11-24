import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Receiver:
    """A merchant's server, which keeps what it gets and answers as told."""

    def __init__(self) -> None:
        self.answers: list[int] = []
        self.got: list[tuple[dict[str, str], bytes]] = []
        self.url = ""

    @contextmanager
    def running(self) -> Iterator["Receiver"]:
        receiver = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                body = self.rfile.read(int(self.headers["Content-Length"]))
                receiver.got.append(
                    ({k.lower(): v for k, v in self.headers.items()}, body)
                )
                status = receiver.answers.pop(0) if receiver.answers else 200
                self.send_response(status)
                if status == 302:
                    self.send_header("Location", "/elsewhere")
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *args: object) -> None:
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{server.server_port}/hook?shop=1"
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            yield self
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
