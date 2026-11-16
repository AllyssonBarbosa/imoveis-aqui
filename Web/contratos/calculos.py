"""Contas do contrato (W12): datas de vencimento e valor de cada parcela.

São funções "puras": recebem números e datas e devolvem números e datas,
sem tocar no banco. Isso deixa a regra fácil de ler e de testar com datas
fixas. Dinheiro é sempre Decimal (nunca float, que erra centavos).
"""

import calendar
from datetime import date, timedelta
from decimal import ROUND_DOWN, Decimal

from django.core.exceptions import ValidationError

CENTAVO = Decimal("0.01")


def somar_meses(data_base, meses, dia):
    """Data no `dia` do mês que fica `meses` depois de `data_base`.

    Se o mês não tem esse dia (29, 30 ou 31 em mês mais curto), cai no
    último dia do mês. A conta parte sempre do dia original, nunca da
    parcela anterior: dia 31 em fevereiro vira 28/02, e em março volta a 31.
    """
    indice = data_base.month - 1 + meses
    ano, mes = data_base.year + indice // 12, indice % 12 + 1
    ultimo_dia = calendar.monthrange(ano, mes)[1]
    return date(ano, mes, min(dia, ultimo_dia))


def meses_de_vigencia(inicio, fim):
    """Quantos meses inteiros tem a vigência da locação (= número de aluguéis).

    A vigência precisa fechar em meses inteiros: N meses depois do início,
    menos um dia. Ex.: 01/01/2027 a 31/12/2027 = 12 meses;
    15/03/2027 a 14/03/2029 = 24 meses.
    """
    if fim <= inicio:
        raise ValidationError({"data_fim": "O fim da vigência precisa ser depois do início."})

    meses = (fim.year - inicio.year) * 12 + (fim.month - inicio.month) + 1
    for candidato in (meses - 1, meses):
        if candidato >= 1 and somar_meses(inicio, candidato, inicio.day) - timedelta(days=1) == fim:
            return candidato

    sugestao = somar_meses(inicio, max(meses - 1, 1), inicio.day) - timedelta(days=1)
    raise ValidationError({
        "data_fim": (
            "A vigência precisa fechar em meses inteiros: termina um dia antes do mesmo dia "
            f"do início (ex.: {inicio:%d/%m/%Y} a {sugestao:%d/%m/%Y})."
        )
    })


def valores_das_parcelas(total, quantidade):
    """Divide `total` em `quantidade` parcelas; os centavos que sobram vão na última.

    Ex.: R$ 1.000,00 em 3 → 333,33 + 333,33 + 333,34. A soma sempre fecha
    com o total, centavo por centavo.
    """
    total = Decimal(total)
    base = (total / quantidade).quantize(CENTAVO, rounding=ROUND_DOWN)
    if base < CENTAVO:
        raise ValidationError(
            {"numero_parcelas": "Parcelas demais para esse valor: cada uma precisa ter pelo menos R$ 0,01."}
        )
    ultima = total - base * (quantidade - 1)
    return [base] * (quantidade - 1) + [ultima]


def vencimentos(data_inicio, quantidade, dia):
    """A 1ª parcela vence no `dia` do mês seguinte ao início; as outras, mês a mês."""
    return [somar_meses(data_inicio, numero, dia) for numero in range(1, quantidade + 1)]
