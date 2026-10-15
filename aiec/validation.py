"""Reglas comerciales compartidas por Claude Desktop y Claude API."""
from decimal import Decimal
import re


def money_values(text):
    pattern = r'(?:\$\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)|\b([0-9][0-9,]*(?:\.[0-9]{1,2})?)\s*MXN\b)'
    return {Decimal((left or right).replace(',', '')) for left, right in re.findall(pattern, text, re.I)}


def validate_commercial_reply(text, knowledge, business):
    amounts = money_values(text)
    if not amounts:
        return
    citations = (knowledge or {}).get('citations', [])
    cited_amounts = set().union(*(money_values(source['quote']) for source in citations))
    if amounts - cited_amounts:
        raise ValueError('La respuesta contiene un importe sin respaldo en sus citas.')
    if re.search(r'\b(?:USD|EUR|GBP|CAD)\b', text, re.I):
        raise ValueError('Las fuentes de precios están en MXN; no se ha verificado una conversión de moneda.')
    for amount in amounts:
        supported = []
        for source in citations:
            if amount not in money_values(source['quote']):
                continue
            urls = set(source.get('source_urls', []))
            for reference in business.get('reference_prices', []):
                if (reference['url'] in urls or reference['url'] in source['quote']) and (
                    reference['provider'].casefold() in text.casefold() and reference['url'] in text
                ):
                    supported.append(reference)
        if not supported or 'referencia' not in text.casefold():
            raise ValueError('Un precio externo necesita su fuente citada, proveedor, enlace y aclaración de referencia.')
