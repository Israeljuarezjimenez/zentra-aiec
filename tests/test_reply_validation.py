import pytest

from aiec.service import catalog
from aiec.validation import validate_commercial_reply

URL='https://graficosimpresos.com/product/35051/'


def test_external_design_fee_can_be_quoted_from_its_verified_source():
    # La fuente documenta un concepto adicional aunque no sea una variante de tiraje.
    knowledge={'citations':[{'quote':'El diseño gráfico tiene un cargo adicional de $490.', 'source_urls':[URL]}]}
    validate_commercial_reply(f'Como referencia, Gráficos Impresos publica $490.00 MXN adicionales por diseño. {URL}',knowledge,catalog())


def test_a_price_from_a_different_source_cannot_use_an_unrelated_provider_link():
    knowledge={'citations':[{'quote':'Otra empresa publica $490.', 'source_urls':['https://example.invalid/precios']}]}
    with pytest.raises(ValueError,match='fuente'):
        validate_commercial_reply(f'Referencia de Gráficos Impresos: $490.00 MXN. {URL}',knowledge,catalog())


def test_currency_conversion_is_not_invented_even_if_the_numeric_amount_matches():
    knowledge={'citations':[{'quote':'Diseño adicional: $490 MXN.', 'source_urls':[URL]}]}
    with pytest.raises(ValueError,match='conversión'):
        validate_commercial_reply(f'Referencia de Gráficos Impresos por $490 USD. {URL}',knowledge,catalog())
