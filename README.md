# amaranth-to-litex

Use amaranth-to-litex to simply import Amaranth code into a Litex project.

Run example:

```
python examples/simple.py
```

CounterStream is an Amaranth module, imported into a Litex project with:
```
self.submodules.cnt = amaranth_to_litex(platform,
    CounterStream(width=26),
)
```

This works by:

1. first compiling the Amaranth module into verilog, see the generated file:

```
less build/CounterStream.v
```

2. then recreating a Litex module with the same interfaces, see the generated file:

```
less build/CounterStream.py
```

Supported interfaces:

* Signal
* Record
* stream.Endpoint (lambdasoc)
