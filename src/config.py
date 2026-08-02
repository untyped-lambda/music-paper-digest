"""設定の一元化.

優先順位: **環境変数 > user.yaml > config.yaml**

- ``config.yaml`` — アプリの既定値のみ(個人情報を含まない)
- ``user.yaml``(任意・コミット可)— 非機密の個人好み。config.yaml の任意キーを
  ディープマージで上書きする。存在しなくてもエラーにはならない
- 環境変数 — 個人情報(宛先メールアドレス等)を注入する

解決ロジック:
  - ``recipient`` = 環境変数 ``DIGEST_RECIPIENT`` があればそれ、なければ
    ``GMAIL_ADDRESS``。どちらも無ければ dry-run 用の既定値
    ``dry-run@example.com`` を使い、例外は出さない
    (本番実行で GMAIL_ADDRESS が無い場合のチェックは呼び出し側の責務)
  - ``sources.openalex.mailto`` は上記で解決した ``recipient`` を流用する
    (専用の設定は持たない)

他モジュール(fetch / prefilter / rank / summarize / render / send /
notify_failure)のシグネチャは変更しない。これらは従来どおり ``config`` dict の
中身(``config["recipient"]`` や ``config["sources"]["openalex"]["mailto"]`` 等)
を参照するだけで、値の出どころが変わったことを意識する必要はない。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = REPO_ROOT / "config.yaml"
DEFAULT_USER_CONFIG_PATH = REPO_ROOT / "user.yaml"

#: 環境変数が無いときに使う、dry-run 用の当たり障りのないダミー宛先
DRY_RUN_PLACEHOLDER_RECIPIENT = "dry-run@example.com"


def _resolve(path_value: str | os.PathLike[str]) -> Path:
    p = Path(path_value)
    return p if p.is_absolute() else REPO_ROOT / p


def _read_yaml_dict(path: Path) -> dict[str, Any]:
    """YAML ファイルを dict として読む。存在しなければ空 dict を返す。"""
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    data = yaml.safe_load(text) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} の形式が不正です(トップレベルは辞書である必要があります)")
    return data


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """``override`` を ``base`` にディープマージした新しい dict を返す。

    両方が dict のキーは再帰的にマージし、それ以外は ``override`` の値で上書きする。
    """
    result = dict(base)
    for key, value in override.items():
        base_value = result.get(key)
        if isinstance(base_value, dict) and isinstance(value, dict):
            result[key] = _deep_merge(base_value, value)
        else:
            result[key] = value
    return result


def resolve_recipient(env: dict[str, str] | None = None) -> str:
    """環境変数から宛先メールアドレスを解決する。

    優先順位: ``DIGEST_RECIPIENT`` > ``GMAIL_ADDRESS`` > dry-run 用既定値。
    """
    environ = env if env is not None else os.environ
    return (
        environ.get("DIGEST_RECIPIENT")
        or environ.get("GMAIL_ADDRESS")
        or DRY_RUN_PLACEHOLDER_RECIPIENT
    )


def load_config(
    config_path: str | os.PathLike[str] | None = None,
    user_config_path: str | os.PathLike[str] | None = None,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """config.yaml → user.yaml → 環境変数の順に解決した設定 dict を返す。

    ``config_path`` / ``user_config_path`` を省略した場合はリポジトリ直下の
    ``config.yaml`` / ``user.yaml`` を使う(user.yaml は無くてもよい)。
    """
    resolved_config_path = _resolve(config_path) if config_path is not None else DEFAULT_CONFIG_PATH
    resolved_user_path = (
        _resolve(user_config_path) if user_config_path is not None else DEFAULT_USER_CONFIG_PATH
    )

    if not resolved_config_path.exists():
        raise FileNotFoundError(f"config.yaml が見つかりません: {resolved_config_path}")

    base_config = _read_yaml_dict(resolved_config_path)
    user_override = _read_yaml_dict(resolved_user_path)
    config = _deep_merge(base_config, user_override)

    recipient = resolve_recipient(env)
    config["recipient"] = recipient

    sources = config.get("sources")
    if not isinstance(sources, dict):
        sources = {}
        config["sources"] = sources
    openalex = sources.get("openalex")
    if not isinstance(openalex, dict):
        openalex = {}
        sources["openalex"] = openalex
    openalex["mailto"] = recipient

    return config
