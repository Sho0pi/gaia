"""Compatibility shim for importing neonize under a protobuf<7 runtime.

neonize 0.3.18 ships protobuf-7.34 *gencode*, but google-adk / mem0ai / a2a-sdk
pin the protobuf *runtime* to <7. The generated ``*_pb2`` modules call
``ValidateProtobufRuntimeVersion`` at import and refuse to load when the runtime
is older than the gencode — even though whatsmeow's messages use only baseline
wire features the older runtime handles fine.

:func:`patch_protobuf_version_guard` neutralises that *version check* (not the
wire format) so neonize and the rest of godpy can share one interpreter. The
clean long-term fix is to run neonize as a sidecar with its own deps (see #5).
"""

from __future__ import annotations


def patch_protobuf_version_guard() -> None:
    """Make protobuf's gencode/runtime version guard a no-op. Call before importing neonize."""
    from google.protobuf import runtime_version

    runtime_version.ValidateProtobufRuntimeVersion = lambda *_a, **_k: None
