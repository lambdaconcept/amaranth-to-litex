# 2023 - LambdaConcept - po@lambdaconcept.com

import os
import sys
import jinja2
import textwrap
import importlib.util
from collections import defaultdict

from amaranth import *
from amaranth.hdl import ir
from amaranth.back.verilog import convert_fragment

from ..interface import stream


__all__ = [
    "amaranth_to_litex",
    "amaranth_pins_from_litex",
    "amaranth_autoconnect_pins",
]


# adapted from https://github.com/amaranth-lang/amaranth/blob/main/amaranth/back/verilog.py
def convert(elaboratable, name="top", platform=None, ports=None, *, emit_src=True,
            strip_internal_attrs=False, return_fragment=False, **kwargs):
    fragment = ir.Fragment.get(elaboratable, platform).prepare(ports=ports, **kwargs)
    verilog_text, name_map = convert_fragment(fragment, name, emit_src=emit_src,
                                              strip_internal_attrs=strip_internal_attrs)
    if return_fragment:
        return verilog_text, fragment

    return verilog_text


def get_ports(elaboratable):
    # Iterate over the elaboratable object to get the list of ports to be
    # exported to the verilog generator.
    # Also records important information inside the metadata dict for later
    # to help reconstruct the python wrapper.

    ports = []
    metadata = defaultdict(dict)

    print("get_ports...")

    for key, value in elaboratable.__dict__.items():
        print(key, type(value), value)

        if isinstance(value, Signal):
            ports.append(value)
            metadata["signals"][key] = value
            metadata["duid"][value.duid] = key # value.name

        elif isinstance(value, stream.Endpoint) or \
             isinstance(value, Record):

            print(value.name)

            for name, _, _ in value.layout:
                field = value[name]

                if isinstance(field, Signal):
                    ports.append(field)
                    metadata["duid"][field.duid] = "{}.{}".format(key, name)

                elif isinstance(field, Record):
                    for subname, _, _ in field.layout:
                        subfield = field[subname]

                        ports.append(subfield)
                        metadata["duid"][subfield.duid] = "{}.{}".format(key, subname)

            if isinstance(value, stream.Endpoint):
                metadata["endpoints"][key] = value
            elif isinstance(value, Record):
                metadata["records"][key] = value

                # we recognise amaranth pins as a special record
                # that contains our private member __litex_pads.
                if hasattr(value, "__litex_pads"):
                    metadata["pins"][key] = value

    print()
    return ports, metadata


def get_record_description(record):
    desc = []
    for name, shape, _ in record.layout:
        desc.append("({!r}, {!r})".format(name, shape))
    return "[{}]".format(", ".join(desc))


def get_endpoint_description(endpoint):
    desc = []
    for name, shape, _ in endpoint.payload.layout:
        desc.append("({!r}, {!r})".format(name, shape))
    return "[{}]".format(", ".join(desc))


def import_pyfile(name, filename):
    spec = importlib.util.spec_from_file_location(name, filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def gen_litex(fragment, metadata, name=None, output_dir=None):
    if output_dir is None:
        output_dir = ""

    params = {}

    # iterate over the instance ports and recreate the signal mapping
    for sig, direction in fragment.ports.items():
        print("sig.name", sig.name, sig.duid)

        if sig.name == "clk":
            value = "ClockSignal()"
        elif sig.name == "rst":
            value = "ResetSignal()"
        else:
            value = "self." + metadata["duid"][sig.duid]

        key = "{}_{}".format(direction, sig.name)
        params[key] = value


    template = """
# Automatically generated by amaranth_to_litex. Do not edit.
import os

from migen import *

from litex.soc.interconnect import stream

class {{classname}}(Module):
    def __init__(self, platform):

        # Signals
    {% for name, sig in signals.items() %}
        self.{{name}} = Signal({{sig.width}})
    {% endfor %}

        # Records
    {% for name, rec in records.items() %}
        self.{{name}} = Record({{get_record_description(rec)}})
    {% endfor %}

        # Endpoints
    {% for name, ep in endpoints.items() %}
        self.{{name}} = stream.Endpoint({{get_endpoint_description(ep)}})
    {% endfor %}

        # # #

        params = dict(
        {% for k, v in params.items() %}
            {{k}} = {{v}},
        {% endfor %}
        )
        self.specials += Instance("{{instancename}}", **params)

        if platform is not None:
            platform.add_source(os.path.join("{{output_dir}}", "{{instancename}}.v"), "verilog")
"""

    source = textwrap.dedent(template).strip()
    compiled = jinja2.Template(source, trim_blocks=True, lstrip_blocks=True)
    output = compiled.render(dict(
        classname=name,
        instancename=name,
        output_dir=output_dir,
        signals=metadata["signals"],
        records=metadata["records"],
        endpoints=metadata["endpoints"],
        params=params,

        # utility functions
        get_record_description=get_record_description,
        get_endpoint_description=get_endpoint_description,
    ))

    # write python file
    filename = os.path.join(output_dir, name + ".py")
    with open(filename, "w") as f:
        f.write(output)

    # import python file
    module = import_pyfile(name, filename)
    return getattr(module, name)


def gen_verilog(elaboratable, name=None, output_dir=None):
    ports, metadata = get_ports(elaboratable)
    print("ports", ports)
    print()
    print("metadata", metadata)
    print()
    ver, frag = convert(elaboratable, name=name, ports=ports,
                      emit_src=False, return_fragment=True)

    # write verilog file
    filename = os.path.join(output_dir, name + ".v")
    with open(filename, "w") as f:
        f.write(ver)

    return frag, metadata


def amaranth_autoconnect_pins(litex_instance):
    statements = []

    # create a lookup table for fast indexing (pin direction)
    lookup_direction = {}
    for sig, direction in litex_instance.__fragment.ports.items():
        lookup_direction[sig.duid] = direction

    for amaranth_pins in litex_instance.__metadata["pins"].values():
        litex_pads = amaranth_pins.__litex_pads

        # create a lookup table for fast indexing (litex pad signal)
        lookup_pads = {}
        for name, shape in litex_pads.layout:
            sig = getattr(litex_pads, name)
            lookup_pads[name] = sig
        print("lookup_pads", lookup_pads)

        # iterate over the amaranth pins
        for name, _, _ in amaranth_pins.layout:
            sig = amaranth_pins[name]
            dot_name = litex_instance.__metadata["duid"][sig.duid]
            pad_name = dot_name.split(".")[-1]

            # get the pad
            pad = lookup_pads[pad_name]
            direction = lookup_direction[sig.duid]

            # find the litex signal
            obj = litex_instance
            for member in dot_name.split("."):
                obj = getattr(obj, member)

            if direction == "i":
                statements.append(obj.eq(pad))
            elif direction == "o":
                statements.append(pad.eq(obj))
            else:
                raise NotImplementedError

            print("sig.name", sig.name, sig.duid, direction, pad_name, pad)

    litex_instance.comb += statements


def amaranth_pins_from_litex(pads):
    rec = Record(pads.layout, name="__pins__" + pads.name)
    # Add a private member to the amaranth pins record to remember
    # this is a conversion from a litex pads record.
    rec.__litex_pads = pads
    return rec


def amaranth_to_litex(platform, elaboratable, name=None, output_dir=None,
                      autoconnect_pins=False):
    if name is None:
        name = elaboratable.__class__.__name__
    if output_dir is None:
        output_dir = "build"

    fragment, metadata = gen_verilog(elaboratable, name=name, output_dir=output_dir)
    litex_class = gen_litex(fragment, metadata, name=name, output_dir=output_dir)

    litex_instance = litex_class(platform)
    # Add private metadata members to the litex instance to remember
    # this is a conversion from an amaranth module.
    # These information will be useful later when matching pins/pads.
    litex_instance.__fragment = fragment
    litex_instance.__metadata = metadata

    if autoconnect_pins:
        amaranth_autoconnect_pins(litex_instance)

    return litex_instance


if __name__ == "__main__":
    from ..cores.counter import *

    ctr = Counter(width=24)
    amaranth_to_litex(None, ctr, name=None, output_dir="")
