"""ZPL for the mock label (live mode uses EasyPost's ZPL) and the raw port-9100 print."""
import socket
from .config import settings


def _clean(s):
    return (s or "").replace("^", " ").replace("~", " ")


def build_label(order, tracking, service, weight_oz, lots):
    a = order["address"]
    f = settings.ship_from
    lines = [
        "^XA^PW812^LL1218^CI28",
        f"^FO40,30^A0N,28,28^FD{_clean(f['name'])}^FS",
        f"^FO40,62^A0N,24,24^FD{_clean(f['street1'])}, {_clean(f['city'])} {f['state']} {f['zip']}^FS",
        "^FO40,110^GB732,3,3^FS",
        f"^FO40,130^A0N,30,30^FDSHIP TO^FS",
        f"^FO40,170^A0N,48,48^FD{_clean(a.get('name'))}^FS",
        f"^FO40,225^A0N,40,40^FD{_clean(a.get('address1'))} {_clean(a.get('address2'))}^FS",
        f"^FO40,275^A0N,40,40^FD{_clean(a.get('city'))}, {_clean(a.get('state'))} {_clean(a.get('zip'))}^FS",
        "^FO40,350^GB732,3,3^FS",
        f"^FO40,375^A0N,34,34^FD{_clean(service)}   {weight_oz:.1f} oz^FS",
        f"^FO40,420^A0N,34,34^FDOrder {_clean(order['name'])}^FS",
        f"^FO70,500^BY2,3,200^BCN,200,Y,N,N^FD{tracking}^FS",  # module width 2 keeps a 22-digit code on a 4in label
        "^FO40,800^GB732,3,3^FS",
        f"^FO40,825^A0N,26,26^FDLots: {_clean(', '.join(lots))[:60]}^FS",
        "^FO40,860^A0N,22,22^FDPOC mock label. Not valid postage.^FS",
        "^XZ",
    ]
    return "\n".join(lines)


def send(zpl, name):
    """file mode: writes var/labels/<name>.zpl. tcp mode: raw ZPL to the Zebra, no driver."""
    if settings.printer_mode == "tcp":
        if not settings.printer_host:
            raise RuntimeError("PRINTER_HOST is not set")
        with socket.create_connection((settings.printer_host, settings.printer_port), timeout=4) as s:
            s.sendall(zpl.encode("utf-8"))
        return f"sent to {settings.printer_host}:{settings.printer_port}"
    settings.out_dir.mkdir(parents=True, exist_ok=True)
    path = settings.out_dir / f"{name}.zpl"
    path.write_text(zpl)
    return f"saved {path.name}"
