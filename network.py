"""Addresses for links opened on another device, rather than on this computer."""
import ipaddress
import socket
from urllib.parse import urlsplit


def is_local_only(host):
    if host.lower().rstrip('.') == 'localhost':
        return True
    try:
        address = ipaddress.ip_address(host)
        return address.is_loopback or address.is_unspecified
    except ValueError:
        return False


def lan_ip():
    """Ask the OS for its outgoing interface. UDP connect sends no packets."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(('192.0.2.1', 80))
            address = sock.getsockname()[0]
            if not is_local_only(address):
                return address
    except OSError:
        pass
    return None


def phone_base_url(request_url, configured_url=''):
    """An explicit public URL wins; local requests use the current LAN IP."""
    source = configured_url.strip() or request_url
    parsed = urlsplit(source)
    if (parsed.scheme not in ('http', 'https') or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment or parsed.path not in ('', '/')):
        raise ValueError('PUBLIC_BASE_URL: http(s)://IP:порт немесе домен енгізіңіз (жолсыз).')
    try:
        port = parsed.port
    except ValueError:
        raise ValueError('PUBLIC_BASE_URL: порт дұрыс емес.') from None
    if not is_local_only(parsed.hostname):
        return source.rstrip('/')
    if configured_url.strip():
        raise ValueError('PUBLIC_BASE_URL ішінде localhost/127.0.0.1 орнына телефоннан ашылатын мекенжайды енгізіңіз.')
    address = lan_ip()
    if not address:
        raise ValueError('Желі мекенжайы табылмады. Wi-Fi қосып, сайтты компьютердің Wi-Fi IP мекенжайымен ашыңыз немесе PUBLIC_BASE_URL орнатыңыз.')
    return '{}://{}{}'.format(parsed.scheme, address, ':' + str(port) if port else '')
