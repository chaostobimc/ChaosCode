"""Proof-of-work helper derived from xtekky/deepseek4free (MIT).

Portions copyright (c) 2025 Tekky; see the accompanying LICENSE. Only the small
WASM-based solver needed by ChaosCode is included. Browser automation and
Cloudflare/CAPTCHA bypass code are deliberately not included.
"""

from __future__ import annotations

import base64
import json
import struct
from pathlib import Path
from typing import Any

import wasmtime

WASM_PATH = Path(__file__).with_name("sha3_wasm_bg.7b9ca65ddd.wasm")


class DeepSeekHash:
    def __init__(self, wasm_path: Path = WASM_PATH) -> None:
        engine = wasmtime.Engine()
        module = wasmtime.Module.from_file(engine, str(wasm_path))
        store = wasmtime.Store(engine)
        linker = wasmtime.Linker(engine)
        linker.define_wasi()
        instance = linker.instantiate(store, module)
        self.store = store
        self.instance = instance
        self.memory = instance.exports(store)["memory"]
        self.exports = instance.exports(store)

    def _write_to_memory(self, text: str) -> tuple[int, int]:
        encoded = text.encode("utf-8")
        pointer = self.exports["__wbindgen_export_0"](self.store, len(encoded), 1)
        self.memory.write(self.store, encoded, pointer)
        return pointer, len(encoded)

    def calculate_hash(
        self,
        algorithm: str,
        challenge: str,
        salt: str,
        difficulty: int,
        expire_at: int,
    ) -> int | None:
        # The upstream WASM ABI accepts the algorithm via the challenge config;
        # the actual SHA3 operation is implemented inside the bundled module.
        del algorithm
        prefix = f"{salt}_{expire_at}_"
        return_pointer = self.exports["__wbindgen_add_to_stack_pointer"](self.store, -16)
        try:
            challenge_pointer, challenge_length = self._write_to_memory(challenge)
            prefix_pointer, prefix_length = self._write_to_memory(prefix)
            self.exports["wasm_solve"](
                self.store,
                return_pointer,
                challenge_pointer,
                challenge_length,
                prefix_pointer,
                prefix_length,
                float(difficulty),
            )
            status = int.from_bytes(
                self.memory.read(self.store, return_pointer, return_pointer + 4),
                byteorder="little",
                signed=True,
            )
            if status == 0:
                return None
            value_bytes = self.memory.read(self.store, return_pointer + 8, return_pointer + 16)
            return int(struct.unpack("<d", value_bytes)[0])
        finally:
            self.exports["__wbindgen_add_to_stack_pointer"](self.store, 16)


class DeepSeekPOW:
    def __init__(self) -> None:
        self.hasher = DeepSeekHash()

    def solve_challenge(self, config: dict[str, Any]) -> str:
        answer = self.hasher.calculate_hash(
            config["algorithm"],
            config["challenge"],
            config["salt"],
            int(config["difficulty"]),
            int(config["expire_at"]),
        )
        result = {
            "algorithm": config["algorithm"],
            "challenge": config["challenge"],
            "salt": config["salt"],
            "answer": answer,
            "signature": config["signature"],
            "target_path": config["target_path"],
        }
        return base64.b64encode(json.dumps(result).encode("utf-8")).decode("ascii")
