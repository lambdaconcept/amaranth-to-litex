# 2023 - LambdaConcept - po@lambdaconcept.com

import os
import sys
import jinja2
import pprint
import logging
import textwrap
import importlib.util
from collections import defaultdict

from amaranth import *
from amaranth.hdl import ir
from amaranth.hdl.rec import *
from amaranth.back.verilog import convert_fragment


__all__ = [
    "amaranth_to_litex",
    "amaranth_signal",
    "amaranth_pins_from_litex",
]


# logging.basicConfig()
logger = logging.getLogger()
# logger.setLevel(logging.DEBUG)
pp = pprint.PrettyPrinter(indent=4, compact=False)


# adapted from https://github.com/amaranth-lang/amaranth/blob/main/amaranth/back/verilog.py
def convert(elaboratable, name="top", platform=None, ports=None, *, emit_src=True,
            strip_internal_attrs=False, return_fragment=False, **kwargs):
    fragment = ir.Fragment.get(elaboratable, platform).prepare(ports=ports, **kwargs)
    verilog_text, name_map = convert_fragment(fragment, name, emit_src=emit_src,
                                              strip_internal_attrs=strip_internal_attrs)
    if return_fragment:
        return verilog_text, fragment

    return verilog_text


def isinstance_endpoint(record):
    # To be more generic we recognise stream endpoints based on their fields
    # rather than matching the class type.
    required = {"valid", "ready", "payload"}
    for field in required:
        if not hasattr(record, field):
            return False
    return True


def get_ports(elaboratable):
    # Iterate over the elaboratable object to get the list of ports to be
    # exported to the verilog generator.
    # Also records important information inside the metadata dict for later
    # to help reconstruct the python wrapper.

    ports = []
    metadata = defaultdict(dict)

    for key, value in elaboratable.__dict__.items():
        logger.debug("member: %s, type: %s, value: %s", key, type(value), value)

        if isinstance(value, Signal):
            ports.append(value)
            metadata["signals"][key] = value
            metadata["duid"][value.duid] = key # value.name

        elif isinstance(value, Record):
            for name, _, _ in value.layout:
                field = value[name]

                if isinstance(field, Signal):
                    ports.append(field)
                    metadata["duid"][field.duid] = ".".join([key, name])

                elif isinstance(field, Record):
                    for subname, _, _ in field.layout:
                        subfield = field[subname]

                        ports.append(subfield)
                        metadata["duid"][subfield.duid] = ".".join([key, name, subname])

            if isinstance_endpoint(value):
                metadata["endpoints"][key] = value
            elif isinstance(value, Record):
                # we recognise amaranth pins as a special record
                # that contains our private member __litex_pads.
                if hasattr(value, "__litex_pads"):
                    metadata["pins"][key] = value
                else:
                    metadata["records"][key] = value

    return ports, metadata


def get_layout_description(layout):
    desc = []

    for name, shape, _ in layout:
        logger.debug("layout: %s, %s", name, shape)

        if isinstance(shape, Layout):
            text = get_layout_description(shape)
        else:
            text = shape

        desc.append("(\"{}\", {})".format(name, text))

    return "[{}]".format(", ".join(desc))


def get_record_description(record):
    return get_layout_description(record.layout)


def get_endpoint_description(endpoint):
    return get_layout_description(endpoint.payload.layout)


def import_pyfile(name, filename):
    spec = importlib.util.spec_from_file_location(name, filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def gen_lhs_rhs(fragment, metadata, pad_name, sig):
    logger.debug("lookup amaranth duid: %s", sig.duid)
    sig_name = "self." + metadata["duid"][sig.duid]

    # get the corresponding direction
    dir = None
    for port, direction in fragment.ports.items():
        if port.duid == sig.duid:
            dir = direction
            break

    if dir == "i":
        lhs = sig_name
        rhs = pad_name
    elif dir == "o":
        lhs = pad_name
        rhs = sig_name
    else:
        raise NotImplementedError

    logger.debug("comb: (%s), %s, %s", dir, lhs, rhs)
    return (lhs, rhs)


def gen_litex_statements(fragment, metadata):
    tristates = []
    connects = []

    # for all pins instances found in the module (usually only one though)
    for key, amaranth_pins in metadata["pins"].items():

        # get the corresponding litex pads
        litex_pads = amaranth_pins.__litex_pads

        # use the amaranth pins for iteration because they contain
        # the most useful information (duid, dir, ...)
        for name, _, _ in amaranth_pins.layout:
            logger.debug("lookup amaranth pin: %s", name)

            sig = amaranth_pins[name]
            pad_name = "{}.{}".format(key, name)

            if isinstance(sig, Signal):
                tuples = [ gen_lhs_rhs(fragment, metadata, pad_name, sig) ]

            # In Amaranth, tristates are automatically instanciated when
            # a platform pin direction is set to "io".
            # In Litex we need to instanciate the tristate manually,
            # we can detect this based on the presence of subsignals "i", "o", "oe".
            elif isinstance(sig, Record):
                if hasattr(sig, "i") and hasattr(sig, "o") and hasattr(sig, "oe"):
                    ts_name = "t_{}".format(name)
                    tristates.append((ts_name, pad_name))

                    tuples = [
                        gen_lhs_rhs(fragment, metadata, ts_name + ".i",  sig.i),
                        gen_lhs_rhs(fragment, metadata, ts_name + ".o",  sig.o),
                        gen_lhs_rhs(fragment, metadata, ts_name + ".oe", sig.oe),
                    ]

                elif hasattr(sig, "i"):
                    tuples = [ gen_lhs_rhs(fragment, metadata, pad_name, sig.i) ]
                elif hasattr(sig, "o"):
                    tuples = [ gen_lhs_rhs(fragment, metadata, pad_name, sig.o) ]

            connects += tuples

    return tristates, connects


def gen_litex(fragment, metadata, name=None, output_dir=None):
    if output_dir is None:
        output_dir = ""

    params = {}

    # iterate over the instance ports and recreate the signal mapping
    for sig, direction in fragment.ports.items():
        logger.debug("sig: %s, duid: %s", sig.name, sig.duid)

        try:
            value = "self." + metadata["duid"][sig.duid]
        except KeyError:
            # Signals that are not listed in the metadata dict are likely
            # special signals like clocks and resets.
            # Valid clock/reset names are "*_clk" and "*_rst", except for the
            # the sys domain named "clk" and "rst".
            domain = sig.name[:-4]
            if not domain:
                domain = "sys"

            if sig.name.endswith("clk"):
                value = "ClockSignal(\"{}\")".format(domain)
            elif sig.name.endswith("rst"):
                value = "ResetSignal(\"{}\")".format(domain)

        key = "{}_{}".format(direction, sig.name)
        params[key] = value

    # auto generate tristates and pins/pads connections
    tristates, connects = gen_litex_statements(fragment, metadata)

    template = """
# Automatically generated by amaranth_to_litex.
#
# ############### DO NOT EDIT. ###############

import os

from migen import *

from litex.soc.interconnect import stream

class {{classname}}(Module):
    def __init__(self, platform):

        # Signals

    {% for name, sig in signals.items() %}
        self.{{name}} = Signal({{sig.width}})
    {% endfor %}

        # Pins

    {% for name, rec in pins.items() %}
        self.{{name}} = Record({{get_record_description(rec)}})
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

    def autoconnect_pads(self, {{", ".join(pins.keys())}}):

        # Tristates

    {% for name, sig in tristates %}
        {{name}} = TSTriple(len({{sig}}))
        self.specials += {{name}}.get_tristate({{sig}})
    {% endfor %}

        # Connect

        self.comb += [
        {% for lhs, rhs in connects %}
            {{lhs}}.eq({{rhs}}),
        {% endfor %}
        ]
"""

    source = textwrap.dedent(template).strip()
    compiled = jinja2.Template(source, trim_blocks=True, lstrip_blocks=True)
    output = compiled.render(dict(
        classname=name,
        instancename=name,
        output_dir=output_dir,
        signals=metadata["signals"],
        pins=metadata["pins"],
        records=metadata["records"],
        endpoints=metadata["endpoints"],
        params=params,
        tristates=tristates,
        connects=connects,

        # utility functions
        get_record_description=get_record_description,
        get_endpoint_description=get_endpoint_description,
    ))

    # write python file
    filename = os.path.join(output_dir, name + ".py")
    os.makedirs(output_dir, exist_ok=True)
    with open(filename, "w") as f:
        f.write(output)

    # import python file
    module = import_pyfile(name, filename)
    return getattr(module, name)


def gen_verilog(elaboratable, name=None, output_dir=None):
    ports, metadata = get_ports(elaboratable)
    logger.debug("ports: \n%s", pp.pformat(ports))
    logger.debug("metadata: %s", pp.pformat(metadata))
    ver, frag = convert(elaboratable, name=name, ports=ports,
                      emit_src=False, return_fragment=True)

    # write verilog file
    filename = os.path.join(output_dir, name + ".v")
    os.makedirs(output_dir, exist_ok=True)
    with open(filename, "w") as f:
        f.write(ver)

    return frag, metadata


def amaranth_signal(*args, **kwargs):
    return Signal(*args, **kwargs)


def amaranth_pins_from_litex(pads, dirs=None):
    if dirs is None:
        dirs = {}

    # Cast the amaranth record layout using the
    # direction hints passed as parameter.
    cast = []
    for name, shape in pads.layout:
        if name in dirs:

            dir = dirs[name]
            subs = []

            # see pin_layout in amaranth/lib/io.py
            if "i" in dir:
                subs.append(("i", shape))
            if "o" in dir:
                subs.append(("o", shape))
            if dir in ["oe", "io"]:
                subs.append(("oe", 1))

            cast.append((name, subs))

        else:
            cast.append((name, shape))

    logger.debug("cast: \n%s", pp.pformat(cast))
    rec = Record(cast, name="__pins__" + pads.name)

    # Add a private member to the amaranth pins record to remember
    # this is a conversion from a litex pads record.
    rec.__litex_pads = pads
    return rec


def amaranth_to_litex(platform, elaboratable, name=None, output_dir=None,
                      autoconnect_pads=True):
    if name is None:
        name = elaboratable.__class__.__name__
    if output_dir is None:
        output_dir = "build"

    fragment, metadata = gen_verilog(elaboratable, name=name, output_dir=output_dir)
    litex_class = gen_litex(fragment, metadata, name=name, output_dir=output_dir)

    litex_instance = litex_class(platform)
    # Add private metadata members to the litex instance to remember
    # this is a conversion from an amaranth module.
    litex_instance.__fragment = fragment
    litex_instance.__metadata = metadata

    if autoconnect_pads:
        pads = [ rec.__litex_pads
            for rec in metadata["pins"].values()
        ]
        litex_instance.autoconnect_pads(*pads)

    return litex_instance


if __name__ == "__main__":
    from ..cores.counter import *

    ctr = Counter(width=24)
    amaranth_to_litex(None, ctr, name=None, output_dir="")
