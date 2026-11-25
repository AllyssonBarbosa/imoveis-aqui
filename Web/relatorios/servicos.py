"""Relatórios da W12 (critério 6). O painel e a API só chamam daqui.

Todos recebem a empresa do usuário logado e só contam o que é dela.
Nada é gravado: os números saem das tabelas na hora da consulta.

- acervo_por_natureza_e_situacao(): quantos imóveis em cada natureza × situação;
- acervo_por_corretor(): quantos imóveis cada corretor tem (Área do gestor do app, APP12);
- contatos_por_corretor(): quantos contatos cada corretor recebeu no período;
- mudancas_de_situacao(): mudanças de situação dos imóveis nos últimos dias (Área do gestor do app, APP12);
- parcelas_em_atraso_e_a_vencer(): parcelas atrasadas e as que vencem nos próximos dias.
"""

from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, Q
from django.utils import timezone

from contas.models import Usuario
from contatos.models import Contato
from contatos.servicos import filtrar_contatos
from contratos.models import Parcela
from imoveis.models import HistoricoSituacao, Imovel
from imoveis.vitrine import FiltroInvalido

DIAS_A_VENCER_PADRAO = 30
DIAS_A_VENCER_MAXIMO = 365
# "Últimos dias" da Área do gestor = hoje e os 6 anteriores, no dia de São Paulo (D-19).
# Mudar a janela do app é mudar só esta constante, sem nova versão do aplicativo.
DIAS_MUDANCAS_PADRAO = 7


def acervo_por_natureza_e_situacao(empresa_id):
    """Tabela natureza × situação, com zeros nas combinações sem imóvel e os totais."""
    contagem = {
        (linha["natureza"], linha["situacao"]): linha["total"]
        for linha in Imovel.objects.filter(empresa_id=empresa_id)
        .values("natureza", "situacao")
        .annotate(total=Count("id"))
    }
    situacoes = Imovel.Situacao.choices
    linhas = []
    for natureza, rotulo in Imovel.Natureza.choices:
        por_situacao = {situacao: contagem.get((natureza, situacao), 0) for situacao, _ in situacoes}
        linhas.append({
            "natureza": natureza, "rotulo": rotulo,
            "por_situacao": por_situacao, "total": sum(por_situacao.values()),
        })
    totais = {situacao: sum(linha["por_situacao"][situacao] for linha in linhas) for situacao, _ in situacoes}
    return {
        "situacoes": [{"situacao": situacao, "rotulo": rotulo} for situacao, rotulo in situacoes],
        "linhas": linhas,
        "totais": totais,
        "total": sum(totais.values()),
    }


def contatos_por_corretor(empresa_id, params):
    """Contatos recebidos por corretor, com quantos ainda aguardam retorno.

    Reaproveita o filtro da caixa de contatos (W11): ?de=AAAA-MM-DD&ate=AAAA-MM-DD
    (datas inclusivas, dia de São Paulo) e ?imovel=<id>. Sem datas = todo o período.
    Corretor ativo sem contato aparece com zero; contato que foi para a empresa
    (corretor desativado na hora do clique) aparece numa linha à parte.
    Pode levantar FiltroInvalido (data em formato errado ou "de" depois de "até").
    """
    contatos = filtrar_contatos(Contato.objects.filter(empresa_id=empresa_id), params)
    agrupado = {
        linha["corretor"]: linha
        for linha in contatos.values("corretor").annotate(
            total=Count("id"), pendentes=Count("id", filter=Q(retorno=Contato.Retorno.PENDENTE))
        )
    }

    linhas = []
    # todos os usuários da empresa que receberam contato, mais os corretores ativos (com zero)
    usuarios = Usuario.objects.filter(empresa_id=empresa_id).filter(
        Q(pk__in=[pk for pk in agrupado if pk is not None])
        | Q(perfil=Usuario.Perfil.CORRETOR, is_active=True)
    ).order_by("nome")
    for usuario in usuarios:
        dados = agrupado.get(usuario.pk, {})
        linhas.append({
            "corretor_id": usuario.pk, "corretor": usuario.nome, "ativo": usuario.is_active,
            "total": dados.get("total", 0), "pendentes": dados.get("pendentes", 0),
        })
    if None in agrupado:
        linhas.append({
            "corretor_id": None, "corretor": "Empresa (corretor desativado no clique)", "ativo": None,
            "total": agrupado[None]["total"], "pendentes": agrupado[None]["pendentes"],
        })
    linhas.sort(key=lambda linha: -linha["total"])  # quem recebeu mais primeiro (empate: ordem do nome)
    return {
        "de": params.get("de") or None,
        "ate": params.get("ate") or None,
        "linhas": linhas,
        "total": sum(linha["total"] for linha in linhas),
        "pendentes": sum(linha["pendentes"] for linha in linhas),
    }


def acervo_por_corretor(empresa_id):
    """Quantos imóveis cada corretor tem (critério 1 da Área do gestor, APP12).

    A mesma base de acervo_por_natureza_e_situacao: todos os imóveis da empresa, em qualquer
    situação, publicados ou não, agrupados por corretor_responsavel; a soma das linhas fecha com o
    `total`. Aparece todo corretor ativo da empresa, mesmo com zero (quem está sem acervo é o que o
    gestor quer ver), e também o usuário desativado que ainda é responsável por imóvel (`ativo` falso).
    Ordem: quem tem mais primeiro e, no empate, por nome.
    """
    imoveis = Imovel.objects.filter(empresa_id=empresa_id)
    agrupado = {
        linha["corretor_responsavel"]: linha["total"]
        for linha in imoveis.values("corretor_responsavel").annotate(total=Count("id"))
    }
    usuarios = Usuario.objects.filter(
        Q(pk__in=list(agrupado))
        | Q(empresa_id=empresa_id, perfil=Usuario.Perfil.CORRETOR, is_active=True)
    ).order_by("nome")
    linhas = [
        {"corretor_id": usuario.pk, "corretor": usuario.nome, "ativo": usuario.is_active,
         "total": agrupado.get(usuario.pk, 0)}
        for usuario in usuarios
    ]
    linhas.sort(key=lambda linha: -linha["total"])  # quem tem mais primeiro (empate: ordem do nome)
    return {"linhas": linhas, "total": imoveis.count()}


def mudancas_de_situacao(empresa_id, dias=DIAS_MUDANCAS_PADRAO, hoje=None):
    """Mudanças de situação dos imóveis da empresa nos últimos `dias` (critério 3 da Área do gestor, APP12).

    Janela inclusiva no dia de São Paulo: de hoje - (dias - 1) até hoje. Entram todas as mudanças do
    HistoricoSituacao, inclusive as gravadas pela assinatura de contrato, da mais recente para a mais
    antiga (ordem do modelo: -alterado_em, -id). Sem paginação: a janela de dias já limita o tamanho.
    """
    hoje = hoje or timezone.localdate()
    desde = hoje - timedelta(days=dias - 1)
    mudancas = (
        HistoricoSituacao.objects.filter(
            imovel__empresa_id=empresa_id, alterado_em__date__gte=desde, alterado_em__date__lte=hoje
        )
        .select_related("imovel", "alterado_por")
        .order_by("-alterado_em", "-id")
    )
    return {"dias": dias, "desde": desde, "mudancas": list(mudancas)}


def ler_dias(valor, padrao=DIAS_A_VENCER_PADRAO):
    """?dias= dos relatórios: de 1 a 365; vazio = `padrao` (30 nas parcelas)."""
    valor = (valor or "").strip()
    if not valor:
        return padrao
    if not valor.isdigit() or not 1 <= int(valor) <= DIAS_A_VENCER_MAXIMO:
        raise FiltroInvalido({"dias": f"Informe um número de dias entre 1 e {DIAS_A_VENCER_MAXIMO}."})
    return int(valor)


def parcelas_em_atraso_e_a_vencer(empresa_id, dias=DIAS_A_VENCER_PADRAO, hoje=None):
    """Parcelas de contratos assinados da empresa:

    - em atraso: venceram antes de hoje e têm saldo (todas, desde a mais antiga);
    - a vencer: vencem de hoje até hoje + `dias` e têm saldo.
    A situação vem de Parcela.objects.com_situacao() — a mesma regra da tela do contrato.
    """
    hoje = hoje or timezone.localdate()
    limite = hoje + timedelta(days=dias)
    base = (
        Parcela.objects.filter(empresa_id=empresa_id)
        .com_situacao(hoje)
        .select_related("contrato__cliente", "contrato__imovel", "contrato__corretor")
        .order_by("vencimento", "contrato_id", "numero")
    )
    em_atraso = list(base.filter(situacao=Parcela.Situacao.ATRASADA))
    a_vencer = list(base.filter(situacao=Parcela.Situacao.A_VENCER, vencimento__lte=limite))
    return {
        "hoje": hoje,
        "dias": dias,
        "limite": limite,
        "em_atraso": em_atraso,
        "a_vencer": a_vencer,
        "saldo_em_atraso": sum((parcela.saldo for parcela in em_atraso), Decimal("0")),
        "saldo_a_vencer": sum((parcela.saldo for parcela in a_vencer), Decimal("0")),
    }
