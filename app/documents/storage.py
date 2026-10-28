from pathlib import Path
from uuid import UUID


class PrivateArtifactStorage:
    """Stores application documents outside the web root using opaque IDs."""

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()

    def write_version(self, application_id: UUID, version: int, files: dict[str, bytes]) -> dict[str, str]:
        if version < 1 or set(files) != {"cv.pdf", "cv.docx", "cover-letter.pdf", "cover-letter.docx"}:
            raise ValueError("Invalid document version or file set")
        destination = (self.root / str(application_id) / str(version)).resolve()
        if not destination.is_relative_to(self.root):
            raise ValueError("Artifact path escaped private storage root")
        destination.mkdir(parents=True, exist_ok=False)
        paths = {}
        for name, content in files.items():
            path = destination / name
            with path.open("xb") as output:
                output.write(content)
            paths[name] = path.relative_to(self.root).as_posix()
        return paths
