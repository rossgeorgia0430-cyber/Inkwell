"""匿名检查 GitHub 稳定发行版并校验下载的安装包。"""

import base64
import hashlib
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from http.client import HTTPException
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from . import __version__

_REPOSITORY = b"a&L2UXJv14XK7(DG&3+QV|ij_axY14Yj<UAYy"
_MAX_METADATA = 1024 * 1024


def _repository() -> str:
    return base64.b85decode(_REPOSITORY).decode("ascii")


def _request(url: str):
    request = Request(url, headers={"User-Agent": "Inkwell", "Accept-Encoding": "identity"})
    return urlopen(request, timeout=20)


def _version(tag: str) -> tuple[int, ...]:
    if not re.fullmatch(r"v?\d{1,6}\.\d{1,6}\.\d{1,6}", tag, re.ASCII):
        raise ValueError("更新服务返回了无法识别的版本号")
    return tuple(map(int, tag.lstrip("v").split(".")))


@dataclass(frozen=True)
class Package:
    tag: str
    name: str
    size: int
    digest: str


def check_release() -> tuple[dict, Package | None]:
    try:
        with _request(f"https://api.github.com/repos/{_repository()}/releases/latest") as response:
            body = response.read(_MAX_METADATA + 1)
        if len(body) > _MAX_METADATA:
            raise ValueError("更新信息过大")
        release = json.loads(body)
        tag = release["tag_name"]
        newer = _version(tag) > _version(__version__)
        if release.get("draft") or release.get("prerelease"):
            raise ValueError("更新服务返回了非正式版本")
        package = None
        name = "Inkwell-macOS.dmg" if sys.platform == "darwin" else "Inkwell-Setup.zip"
        for asset in release["assets"] if newer else ():
            if asset["name"] != name or asset.get("state") != "uploaded":
                continue
            digest = asset.get("digest") or ""
            if not re.fullmatch(r"sha256:[a-f0-9]{64}", digest) or not 0 < asset["size"] <= 1024**3:
                raise ValueError("安装包缺少有效的大小或 SHA-256 校验信息")
            package = Package(tag, name, asset["size"], digest[7:])
            break
        return {"ok": True, "current": __version__, "latest": tag, "newer": newer,
                "downloadable": package is not None}, package
    except HTTPError as exc:
        if exc.code in (403, 429):
            raise ValueError("更新服务暂时限制访问，请稍后重试") from exc
        raise ValueError(f"更新服务返回 HTTP {exc.code}") from exc
    except (OSError, HTTPException) as exc:
        raise ValueError("无法连接更新服务，请检查网络后重试") from exc
    except (KeyError, TypeError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("更新服务返回的数据格式无效") from exc


def download_package(package: Package, destination: Path) -> None:
    url = f"https://github.com/{_repository()}/releases/download/{package.tag}/{package.name}"
    temporary = None
    try:
        with _request(url) as response, tempfile.NamedTemporaryFile(
                dir=destination.parent, delete=False) as output:
            temporary = Path(output.name)
            checksum = hashlib.sha256()
            size = 0
            while chunk := response.read(256 * 1024):
                size += len(chunk)
                if size > package.size:
                    raise ValueError("安装包大小与发行信息不符")
                checksum.update(chunk)
                output.write(chunk)
        if size != package.size or checksum.hexdigest() != package.digest:
            raise ValueError("安装包校验失败，请重新下载")
        os.replace(temporary, destination)
    except (OSError, HTTPException) as exc:
        raise ValueError("安装包下载或写入失败，请检查网络和保存位置") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
