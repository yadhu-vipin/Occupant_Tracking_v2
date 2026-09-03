"""Make the pip-installed CUDA 12 runtime discoverable to onnxruntime-gpu.

onnxruntime-gpu 1.22's provider DLL depends on ``cublasLt64_12.dll`` /
``cudnn64_9.dll`` etc., which the ``nvidia-*-cu12`` wheels drop under
``site-packages/nvidia/<lib>/bin`` -- a directory ORT 1.22 does not search on
its own. Call :func:`enable_cuda` once before creating any inference session.

**These directories must go on ``PATH``, not just ``os.add_dll_directory()``.**
ORT loads ``onnxruntime_providers_cuda.dll`` itself via ``LoadLibraryEx``, and
the Windows loader does not consult ``add_dll_directory`` entries when resolving
*that* DLL's own imports -- so cuBLAS is reported missing while sitting in a
registered directory. Prepending to ``PATH`` is what actually resolves it; the
``add_dll_directory`` calls are kept as belt-and-braces for direct loads.
"""
import os
import sys
from pathlib import Path


def enable_cuda():
    """Put every ``site-packages/nvidia/*/bin`` on ``PATH`` (and the DLL search
    path) so ORT can resolve the CUDA 12 runtime. Idempotent. No-op off Windows
    or when the wheels aren't installed. Returns the directories added."""
    if sys.platform != "win32":
        return []
    added = []
    for entry in sys.path:
        nvidia = Path(entry) / "nvidia"
        if not nvidia.is_dir():
            continue
        for bin_dir in sorted(nvidia.glob("*/bin")):
            d = str(bin_dir)
            try:
                os.add_dll_directory(d)
            except OSError:
                pass
            added.append(d)

    path = os.environ.get("PATH", "")
    missing = [d for d in added if d not in path.split(os.pathsep)]
    if missing:
        os.environ["PATH"] = os.pathsep.join(missing) + os.pathsep + path
    return added


if __name__ == "__main__":
    from pathlib import Path as _P

    dirs = enable_cuda()
    print(f"added {len(dirs)} CUDA dll dir(s)")
    for d in dirs:
        print("  ", d)

    import onnxruntime as ort
    print("onnxruntime", ort.__version__)

    model = _P.home() / ".insightface" / "models" / "buffalo_l" / "w600k_r50.onnx"
    if model.exists():
        sess = ort.InferenceSession(str(model),
                                    providers=["CUDAExecutionProvider", "CPUExecutionProvider"])
        print("recognition model bound to:", sess.get_providers()[0])
    else:
        print("(buffalo_l not downloaded yet -- run a pipeline step first)")
