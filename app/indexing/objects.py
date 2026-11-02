"""원본 PDF와 결과 파일 저장소. 운영은 S3, 로컬은 LocalStack(S3_ENDPOINT) 또는 디렉터리."""
from pathlib import Path
from typing import Protocol


class ObjectStore(Protocol):
    def get(self, bucket: str, key: str) -> bytes: ...
    def put(self, bucket: str, key: str, data: bytes, content_type: str) -> None: ...


class DirStore:
    """<root>/<bucket>/<key>. 테스트·S3 없이 확인할 때."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def _path(self, bucket: str, key: str) -> Path:
        p = (self.root / bucket / key).resolve()
        if self.root.resolve() not in p.parents:
            raise ValueError(f"허용되지 않은 경로: {bucket}/{key}")
        return p

    def get(self, bucket: str, key: str) -> bytes:
        return self._path(bucket, key).read_bytes()

    def put(self, bucket: str, key: str, data: bytes, content_type: str) -> None:
        p = self._path(bucket, key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)


class S3Store:
    def __init__(self, endpoint_url: str | None = None, region: str = "ap-northeast-2"):
        import boto3
        self.s3 = boto3.client("s3", endpoint_url=endpoint_url or None, region_name=region)

    def get(self, bucket: str, key: str) -> bytes:
        return self.s3.get_object(Bucket=bucket, Key=key)["Body"].read()

    def put(self, bucket: str, key: str, data: bytes, content_type: str) -> None:
        self.s3.put_object(Bucket=bucket, Key=key, Body=data, ContentType=content_type)
