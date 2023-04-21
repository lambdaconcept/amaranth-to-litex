# amaranth-to-litex

Use amaranth-to-litex to simply import Amaranth code into a Litex project.

### Exported interfaces

The converter automatically collects the interfaces contained in self.__ dict __:

* Signals
* Records
* Endpoints (streams)
* Pins (platform.request())
* Constants

### Quick start

Run example:

```
python examples/usb.py
```

__CounterStream__ is an Amaranth module, imported into a Litex project with:
```
self.submodules.cnt = amaranth_to_litex(platform,
    CounterStream(width=26),
)
```

__USBGenericDevice__ is a LUNA based device, imported in Litex:
```
litex_pads = platform.request("ulpi")
usb_pins = amaranth_pins_from_litex(litex_pads, {
    "data": "io",
    "clk" : "i",
    "dir" : "i",
})

self.submodules.dev = dev = amaranth_to_litex(platform,
    USBGenericDevice(
        pins=usb_pins,
        vid=0xffff, pid=0x1234,
    ),
)
```

This works by:

1. first compiling the Amaranth module into verilog, see the generated files:

```
less build/CounterStream.v
less build/USBGenericDevice.v
```

2. then recreating a Litex module with the same interfaces, see the generated file:

```
less build/CounterStream.py
less build/USBGenericDevice.py
```

### API Reference

* Convert an Amaranth module to a Litex module:
```
self.submodules.litex_module = amaranth_to_litex(platform, MyAmaranthModule()):
```

* Create an Amaranth signal:
```
amaranth_sig = amaranth_signal(name="my_signal")
```

* Convert Litex platform pins to Amaranth pins:
```
litex_pads = platform.request("spi")
amaranth_pins = amaranth_pins_from_litex(litex_pads)
```
