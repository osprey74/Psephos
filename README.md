# Psephos

> A programmable scientific calculator for ClockworkPi PicoCalc, named after **ψῆφος** (psephos) — the small pebbles used for counting in ancient Greece.

[日本語版はこちら / Japanese README](README.ja.md)

- **Target hardware**: ClockworkPi PicoCalc + Raspberry Pi Pico 2W (RP2350)
- **Runtime**: MicroPython (LofiFren / zenodante firmware family)
- **License**: MIT
- **Status**: MVP — core logic verified on PC fallback, hardware integration pending

## What it does

Psephos turns the PicoCalc's physical QWERTY and retro display into a programmable scientific calculator. You type Python-style expressions directly:

```
> sin(pi/6) + sqrt(2)
> 2 ** 10
> degrees(pi)
> ans * 1.5
```

The differentiator: **calculation history accumulates on screen and persists to the SD card** as `/sd/psephos_history.txt`. Past calculations survive power-off and reload at startup.

## Allowed functions / constants

Only names explicitly whitelisted by `_build_namespace()` are callable. `eval` is sandboxed via `{"__builtins__": {}}`.

- **Trigonometry**: `sin cos tan asin acos atan atan2`
- **Exponential / log**: `exp log log10 sqrt pow`
- **Rounding**: `floor ceil fabs abs round`
- **Angle**: `radians degrees`
- **Constants**: `pi e tau`
- **Utility**: `min max ans`

## Documentation

- [DESIGN.md](DESIGN.md) — Design SSOT (architecture, security model, screen layout, data design)
- [HANDOFF_psephos.md](HANDOFF_psephos.md) — Implementation handoff (Phase 1 hardware adaptation tasks)

## Roadmap

- **Phase 1** (current) — On-device adaptation: keyboard API, LUT color verification, SD persistence
- **Phase 2** — History recall / re-edit via arrow keys, in-line cursor editing
- **Phase 3** — User-defined variables, hex/binary input/output
- **Phase 4** — Theme switching, function reference screen, config file, history rotation

## References

- [ClockworkPi PicoCalc](https://www.clockworkpi.com/picocalc)
- [zenodante/PicoCalc-micropython-driver](https://github.com/zenodante/PicoCalc-micropython-driver)
- [LofiFren/PicoCalc](https://github.com/LofiFren/PicoCalc)
- [MicroPython `math` module](https://docs.micropython.org/en/latest/library/math.html)
