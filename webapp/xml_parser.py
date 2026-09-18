"""
Parser de facturas electrónicas SUNAT (UBL 2.1).
Soporta: Factura (01), Boleta (03), Nota de Crédito (07), Nota de Débito (08).
"""
import re
import xml.etree.ElementTree as ET

_MEMBER_RE = re.compile(r'^(IPIDET-\d+)', re.IGNORECASE)

_NS = {
    'cbc': 'urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2',
    'cac': 'urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2',
    'ext': 'urn:oasis:names:specification:ubl:schema:xsd:CommonExtensionComponents-2',
}

_TIPO_MAP = {'01': 'factura', '03': 'boleta', '07': 'recibo', '08': 'recibo'}


def _txt(el, path: str) -> str:
    if el is None:
        return ''
    node = el.find(path, _NS)
    return node.text.strip() if node is not None and node.text else ''


def _float(s: str) -> float:
    try:
        return float(s) if s else 0.0
    except ValueError:
        return 0.0


def _tipo_from_numero(numero: str) -> str:
    prefix = (numero or '').upper()[:1]
    return 'factura' if prefix == 'F' else 'boleta' if prefix == 'B' else 'factura'


def parse_sunat_xml(content: bytes) -> dict:
    """
    Parsea XML de comprobante electrónico SUNAT.
    Retorna dict con: numero, tipo, fecha_emision, ruc_empresa, razon_social,
                      monto_total, items, emisor_ruc, emisor_nombre.
    Lanza ValueError si el XML es inválido o no tiene número.
    """
    try:
        root = ET.fromstring(content)
    except ET.ParseError as e:
        raise ValueError(f"XML inválido: {e}")

    # Tag sin namespace para detectar tipo de documento
    raw_tag = root.tag.split('}')[1] if '}' in root.tag else root.tag

    numero     = _txt(root, 'cbc:ID')
    fecha      = _txt(root, 'cbc:IssueDate')
    tipo_code  = _txt(root, 'cbc:InvoiceTypeCode')
    tipo       = _TIPO_MAP.get(tipo_code) or _tipo_from_numero(numero)

    # Emisor (IPIDET)
    emisor = root.find('cac:AccountingSupplierParty/cac:Party', _NS)
    emisor_ruc    = _txt(emisor, 'cac:PartyTaxScheme/cbc:CompanyID')
    emisor_nombre = _txt(emisor, 'cac:PartyLegalEntity/cbc:RegistrationName')

    # Receptor (empresa o persona natural)
    receptor     = root.find('cac:AccountingCustomerParty/cac:Party', _NS)
    ruc_empresa  = _txt(receptor, 'cac:PartyTaxScheme/cbc:CompanyID')
    razon_social = _txt(receptor, 'cac:PartyLegalEntity/cbc:RegistrationName')
    if not razon_social:
        razon_social = _txt(receptor, 'cac:PartyName/cbc:Name')
    if not ruc_empresa:
        # Boleta: puede traer DNI en PartyIdentification
        ruc_empresa = _txt(receptor, 'cac:PartyIdentification/cbc:ID')

    monto_total = _float(_txt(root, 'cac:LegalMonetaryTotal/cbc:PayableAmount'))

    # Líneas según tipo de documento
    if raw_tag == 'CreditNote':
        line_tag = 'cac:CreditNoteLine'
        qty_tag  = 'cbc:CreditedQuantity'
    elif raw_tag == 'DebitNote':
        line_tag = 'cac:DebitNoteLine'
        qty_tag  = 'cbc:DebitedQuantity'
    else:
        line_tag = 'cac:InvoiceLine'
        qty_tag  = 'cbc:InvoicedQuantity'

    items = []
    for line in root.findall(line_tag, _NS):
        desc     = _txt(line, 'cac:Item/cbc:Description')
        subtotal = _float(_txt(line, 'cbc:LineExtensionAmount'))
        price    = _float(_txt(line, 'cac:Price/cbc:PriceAmount'))
        qty_node = line.find(qty_tag, _NS)
        qty      = _float(qty_node.text if qty_node is not None else '1')
        # Código interno del producto (SellersItemIdentification)
        codigo_sunat = _txt(line, 'cac:Item/cac:SellersItemIdentification/cbc:ID')
        # Auto-detectar member_id si la descripción empieza con IPIDET-XXXX
        m = _MEMBER_RE.match(desc)
        member_id = m.group(1).upper() if m else None
        items.append({
            'producto_nombre': desc,
            'codigo_sunat':    codigo_sunat,
            'cantidad':        qty,
            'precio_unitario': price,
            'monto':           subtotal,
            'member_id':       member_id,
        })

    return {
        'numero':        numero,
        'tipo':          tipo,
        'fecha_emision': fecha,
        'ruc_empresa':   ruc_empresa,
        'razon_social':  razon_social,
        'monto_total':   monto_total,
        'items':         items,
        'emisor_ruc':    emisor_ruc,
        'emisor_nombre': emisor_nombre,
    }
