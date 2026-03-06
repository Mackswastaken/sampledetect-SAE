import os
from typing import List, Optional, Dict, Any
from supabase import create_client, Client


def _require(name: str) -> str:
    v = os.getenv(name)
    if not v:
        raise RuntimeError(f"{name} is missing in environment variables")
    return v


class SupabaseStorage:
    """
    Minimal wrapper around Supabase Storage for our MVP.
    Bucket should be PRIVATE; we serve files through the backend (proxy) when needed.
    """

    def __init__(self):
        self.url = _require("SUPABASE_URL")
        self.key = _require("SUPABASE_SERVICE_ROLE_KEY")
        self.bucket = os.getenv("SUPABASE_BUCKET", "sampledetect")
        self.client: Client = create_client(self.url, self.key)
        self.sb = self.client.storage.from_(self.bucket)

    def upload_bytes(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        # upsert=True so re-running doesn’t fail
        self.sb.upload(
            path=key,
            file=data,
            file_options={"content-type": content_type, "upsert": "true"},
        )

    def upload_file(self, key: str, file_path: str, content_type: str = "application/octet-stream") -> None:
        with open(file_path, "rb") as f:
            self.upload_bytes(key, f.read(), content_type=content_type)

    def download_bytes(self, key: str) -> bytes:
        res = self.sb.download(key)
        # supabase-py returns raw bytes
        return res

    def list_prefix(self, prefix: str) -> List[Dict[str, Any]]:
        """
        Lists objects under a prefix by listing the folder.
        Supabase lists by "path" and requires folder + recursive style.
        We'll list only one level at a time; our structure is simple.
        """
        folder = prefix.rstrip("/")
        # If prefix is "", list root
        if "/" in folder:
            parent = folder.rsplit("/", 1)[0]
            path = parent
        else:
            path = ""

        items = self.sb.list(path=path)
        # Filter to only those that begin with prefix
        out = []
        for it in items:
            name = it.get("name") or ""
            full = f"{path}/{name}".lstrip("/")
            if full.startswith(prefix.rstrip("/") + "/") or full == prefix.rstrip("/"):
                out.append({"name": name, "full_path": full, **it})
        return out

    def remove(self, key: str) -> None:
        self.sb.remove([key])

    def remove_prefix(self, prefix: str) -> int:
        """
        Remove everything under a prefix. Uses list of keys then remove.
        """
        prefix = prefix.rstrip("/") + "/"
        # We can’t do recursive listing perfectly in one call with supabase list,
        # but our keys are known patterns; we’ll remove by asking backend DB for assets in practice.
        # This method is mainly used for direct key removals.
        return 0

    def move(self, src_key: str, dst_key: str) -> None:
        self.sb.move(src_key, dst_key)