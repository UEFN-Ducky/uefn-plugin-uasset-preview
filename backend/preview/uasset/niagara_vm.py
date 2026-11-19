"""Read what the panel needs to run a Niagara emitter's particle scripts itself.

Each NiagaraScript keeps its compiled program in the tagged ``CachedScriptVM``
(NiagaraVMExecutableData): the classic VectorVM ``ByteCode``, the number of temporary
registers, the particle ``Attributes``, and the ``Parameters`` / ``InternalParameters``
that make up the constant buffer. Their entries (NiagaraVariable) are 98 bytes: the
name, a type description whose struct import sits at +33, then (with a value) an int32
size and the bytes.

The constant buffer the bytecode addresses (operands are byte offsets) was worked out
on NS_Cadeaux_Magic and holds for its scripts:

    [engine block: 160 floats] [script Parameters, packed in order] [InternalParameters]

and an interpolated spawn script carries a second copy of the first two parts (the
previous frame) before the internal constants. Engine block: FNiagaraGlobalParameters
(0..7: world dt, dt, 1/dt, time, real time, quality level), FNiagaraSystemParameters
(8..23), FNiagaraOwnerParameters (24..151: six 4x4 matrices, then rotation 120,
position 124, velocity 128, X/Y/Z axes, scale 144, LWC tile), FNiagaraEmitterParameters
(152 particle count, 153 total spawned, 154 spawn count scale, 155 age, 156 random seed,
157 instance seed). Checked: the spawn script lerps Engine.Owner.Position at 124, builds
UniqueID from 153, writes Lifetime and Color from 160..164.

System scripts address a smaller buffer, [FNiagaraGlobalParameters: 8 floats]
[Parameters] [InternalParameters], where the emitters' loop settings are baked into the
internal constants (checked on NS_Aura_Two_Colors: the offsets used land on the loop
durations 5, 2, 0.25 and delay 0.7). They read the engine and User parameters through a
second data set (``DataSetToParameters``) with the same inputdata ops, and write each
emitter's ``<Emitter>.<Module>.SpawnOutputInfo`` (FNiagaraSpawnInfo: int Count, float
InterpStartDt, float IntervalDt, int SpawnGroup) into the system instance's attributes.
"""

from __future__ import annotations

import base64
import struct
from typing import Any, Optional

from .package import Package
from .props import Raw, Ref, export_properties
from .reader import ReadError

ENGINE_FLOATS = 160
SYSTEM_ENGINE_FLOATS = 8
# (float components, int components) of struct types that mix both
_MIXED = {"NiagaraSpawnInfo": (2, 2)}
# components per value, and whether the value is integer (bit pattern kept as is)
_TYPE_SIZE = {
    "NiagaraFloat": (1, False), "NiagaraInt32": (1, True), "NiagaraBool": (1, True),
    "Vector2f": (2, False), "Vector2D": (2, False), "Vector3f": (3, False), "Vector": (3, False),
    "NiagaraPosition": (3, False), "Vector4f": (4, False), "Vector4": (4, False), "LinearColor": (4, False),
    "Quat4f": (4, False), "Quat": (4, False), "NiagaraID": (2, True), "NiagaraMatrix": (16, False),
}


def _variables(pkg: Package, raw: bytes, with_data: bool) -> list[tuple[str, str, bytes]]:
    count = struct.unpack_from("<i", raw, 0)[0]
    pos, out = 4, []
    for _ in range(count):
        index, number = struct.unpack_from("<ii", raw, pos)
        if not 0 <= index < len(pkg.names):
            raise ReadError("bad variable name in compiled script")
        name = pkg.names[index] + (f"_{number - 1}" if number else "")
        ref = struct.unpack_from("<i", raw, pos + 33)[0]
        kind = pkg.imports[-ref - 1].object_name if -len(pkg.imports) <= ref < 0 else ""
        pos += 98
        value = b""
        if with_data:
            size = struct.unpack_from("<i", raw, pos)[0]
            if not 0 <= size <= 256:
                raise ReadError("bad variable value in compiled script")
            value = raw[pos + 4:pos + 4 + size]
            pos += 4 + size
        out.append((name, kind, value))
    if pos != len(raw):
        raise ReadError("compiled script variables do not add up")
    return out


def _array(pkg: Package, block: Any, with_data: bool) -> list[tuple[str, str, bytes]]:
    if isinstance(block, dict):
        block = block.get("Parameters")
    if isinstance(block, Raw):
        return _variables(pkg, block.data, with_data)
    return []


def _components(kind: str) -> tuple[int, int]:
    """(float components, int components) a value of this Niagara type takes in a data set."""
    if kind in _MIXED:
        return _MIXED[kind]
    if kind.startswith("E"):  # enums (ENiagaraExecutionState...) are int32
        return 0, 1
    size, is_int = _TYPE_SIZE.get(kind, (1, False))
    return (0, size) if is_int else (size, 0)


def rapid_raw(pkg: Package, script_props: dict[str, Any]) -> dict[str, bytes]:
    """{"Constants.<Emitter>.<Module>.<Input>": value bytes} from one script's store."""
    return store_raw(pkg, script_props.get("RapidIterationParameters"))


def store_raw(pkg: Package, store: Any, prefix: str = "Constants.") -> dict[str, bytes]:
    """{name: value bytes} of a parameter store (rapid-iteration, or the exposed User ones)."""
    if not isinstance(store, dict) or not isinstance(store.get("SortedParameterOffsets"), Raw):
        return {}
    table, block = store["SortedParameterOffsets"].data, bytes(store.get("ParameterData") or [])
    starts = [(pos, pkg.names[i]) for pos in range(4, len(table) - 7)
              for i, n in [struct.unpack_from("<ii", table, pos)]
              if n == 0 and 0 <= i < len(pkg.names) and pkg.names[i].startswith(prefix)]
    items = []
    for k, (pos, name) in enumerate(starts):
        end = starts[k + 1][0] if k + 1 < len(starts) else len(table)
        offset = struct.unpack_from("<i", table, end - 4)[0] if end - pos >= 12 else -1
        if 0 <= offset < len(block):
            items.append((offset, name))
    items.sort()
    return {name: block[off:(items[k + 1][0] if k + 1 < len(items) else len(block))] for k, (off, name) in enumerate(items)}


def compiled_script(pkg: Package, script_ref: Any, engine_floats: int = ENGINE_FLOATS,
                    user: Optional[dict[str, bytes]] = None) -> Optional[dict[str, Any]]:
    """The program and constant buffer of one particle script, for ui/niagara_sim.js."""
    if not isinstance(script_ref, Ref) or script_ref.index <= 0:
        return None
    props = export_properties(pkg, pkg.exports[script_ref.index - 1])
    vm = props.get("CachedScriptVM")
    if not isinstance(vm, dict):
        return None
    code = bytes((vm.get("ByteCode") or {}).get("Data") or [])
    if not code:
        return None
    usage = str(props.get("Usage") or "")
    interpolated = usage.endswith("Interpolated")
    rapid = rapid_raw(pkg, props)
    params, internal = _array(pkg, vm.get("Parameters"), True), _array(pkg, vm.get("InternalParameters"), True)
    buffer = bytearray(4 * engine_floats)
    named: dict[str, list[int]] = {}
    for name, kind, value in params:
        size = _TYPE_SIZE.get(kind, (max(1, len(value) // 4), False))[0]
        value = rapid.get(name) or (user or {}).get(name) or value  # module inputs, then the system's User values
        named[name] = [len(buffer) // 4, size]
        buffer += (value + bytes(4 * size))[:4 * size]
    half = len(buffer) // 4
    if interpolated:
        buffer += bytes(buffer)  # the previous frame's copy
    for _name, _kind, value in internal:
        buffer += value + bytes(-len(value) % 4)
    return {
        "usage": usage.rsplit("::", 1)[-1],
        "code": base64.b64encode(code).decode(),
        "temps": int(vm.get("NumTempRegisters") or 0),
        "constants": base64.b64encode(bytes(buffer)).decode(),
        "half": half,
        "interpolated": interpolated,
        "params": named,
        "engine": engine_floats,
        "externals": _externals(pkg, props, vm),
    }


def _layout(variables: list[tuple[str, str, bytes]]) -> list[dict[str, Any]]:
    """Data set layout: each variable's float and int components, in order."""
    floats = ints = 0
    out = []
    for name, kind, value in variables:
        nf, ni = _components(kind)
        entry: dict[str, Any] = {"name": name, "type": kind, "f": floats, "nf": nf, "i": ints, "ni": ni}
        if nf and not ni:
            entry.update(offset=floats, size=nf, int=False)
        elif ni and not nf:
            entry.update(offset=ints, size=ni, int=True)
        if value:
            entry["value"] = base64.b64encode(value).decode()
        out.append(entry)
        floats += nf
        ints += ni
    return out


def system_program(pkg: Package, system_props: dict[str, Any]) -> Optional[dict[str, Any]]:
    """The system spawn / update scripts, their data set and parameter data set, for the panel."""
    spawn_ref, update_ref = system_props.get("SystemSpawnScript"), system_props.get("SystemUpdateScript")
    spawn = compiled_script(pkg, spawn_ref, SYSTEM_ENGINE_FLOATS)
    update = compiled_script(pkg, update_ref, SYSTEM_ENGINE_FLOATS)
    if spawn is None or update is None:
        return None
    vm = export_properties(pkg, pkg.exports[update_ref.index - 1]).get("CachedScriptVM") or {}
    user = store_raw(pkg, system_props.get("ExposedParameters"), "User.")
    params: list[tuple[str, str, bytes]] = []
    for entry in vm.get("DataSetToParameters") or []:
        if isinstance(entry, tuple) and len(entry) == 2:
            params = [(n, t, user.get(n, v)) for n, t, v in _array(pkg, entry[1], True)]
            break
    return {"spawn": spawn, "update": update,
            "layout": _layout(_array(pkg, vm.get("Attributes"), False)), "params": _layout(params)}


def _externals(pkg: Package, props: dict[str, Any], vm: dict[str, Any]) -> list[dict[str, Any]]:
    """The external functions the bytecode calls (op external_func_call + u16 index), each with
    its operand counts and the data its data interface holds (curve tables, sprite renderer)."""
    interfaces = {}
    for entry in props.get("CachedDefaultDataInterfaces") or []:
        ref = entry.get("DataInterface") if isinstance(entry, dict) else None
        if isinstance(ref, Ref) and ref.index > 0:
            interfaces[str(entry.get("CompileName") or entry.get("Name") or "")] = ref.index
    out = []
    for fn in vm.get("CalledVMExternalFunctions") or []:
        if not isinstance(fn, dict):
            continue
        item: dict[str, Any] = {"name": str(fn.get("Name") or ""), "owner": str(fn.get("OwnerName") or ""),
                                "inputs": [bool(x) for x in fn.get("InputParamLocations") or []],
                                "outputs": int(fn.get("NumOutputs") or 0)}
        index = interfaces.get(item["owner"])
        if index:
            exp = pkg.exports[index - 1]
            item["interface"] = pkg.class_name(exp)
            di = export_properties(pkg, exp)
            lut = di.get("ShaderLUT")
            if isinstance(lut, list) and lut:
                low, high = float(di.get("LUTMinTime") or 0.0), di.get("LUTMaxTime")
                item["lut"] = [float(v) for v in lut]
                item["samples"] = int(float(di.get("LUTNumSamplesMinusOne") or 0)) + 1
                item["min"] = low
                item["max"] = float(high) if isinstance(high, (int, float)) else 1.0
            renderer = di.get("SpriteRenderer")
            if isinstance(renderer, Ref) and renderer.index > 0:
                sub = export_properties(pkg, pkg.exports[renderer.index - 1]).get("SubImageSize")
                item["sub_image"] = sub if isinstance(sub, list) and len(sub) == 2 else [1.0, 1.0]
        out.append(item)
    return out


def particle_layout(pkg: Package, script_ref: Any) -> list[dict[str, Any]]:
    """Particle attributes with their float / int component offsets in the data set."""
    if not isinstance(script_ref, Ref) or script_ref.index <= 0:
        return []
    vm = export_properties(pkg, pkg.exports[script_ref.index - 1]).get("CachedScriptVM")
    if not isinstance(vm, dict):
        return []
    return _layout(_array(pkg, vm.get("Attributes"), False))
