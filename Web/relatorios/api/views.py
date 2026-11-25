"""Relatórios na API (para o app). Só o gestor; sempre a empresa dele.

Valores em dinheiro saem como texto ("1234.50"), igual ao resto da API,
para não perder centavos com número de ponto flutuante.
"""

from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from core.permissions import EhGestor
from imoveis.vitrine import FiltroInvalido

from ..servicos import (
    DIAS_MUDANCAS_PADRAO, acervo_por_corretor, acervo_por_natureza_e_situacao, contatos_por_corretor,
    ler_dias, mudancas_de_situacao, parcelas_em_atraso_e_a_vencer,
)


class RelatorioApiView(APIView):
    permission_classes = [EhGestor]


class AcervoApiView(RelatorioApiView):
    """GET /api/relatorios/acervo/ — imóveis por natureza × situação."""

    def get(self, request):
        return Response(acervo_por_natureza_e_situacao(request.user.empresa_id))


class AcervoPorCorretorApiView(RelatorioApiView):
    """GET /api/relatorios/acervo-por-corretor/ — quantos imóveis cada corretor tem (critério 1)."""

    def get(self, request):
        return Response(acervo_por_corretor(request.user.empresa_id))


class ContatosApiView(RelatorioApiView):
    """GET /api/relatorios/contatos/?de=AAAA-MM-DD&ate=AAAA-MM-DD — contatos por corretor."""

    def get(self, request):
        try:
            return Response(contatos_por_corretor(request.user.empresa_id, request.query_params))
        except FiltroInvalido as erro:
            raise ValidationError(erro.erros)


def _mudanca(historico):
    """Lista fechada de campos (D-20): nada de proprietário, preço nem dado interno do imóvel."""
    imovel = historico.imovel
    return {
        "id": historico.pk,
        "imovel_id": imovel.pk,
        "imovel_codigo": imovel.codigo,
        "imovel_titulo": imovel.titulo,
        "situacao_anterior": historico.situacao_anterior,
        "situacao_nova": historico.situacao_nova,
        "alterado_por": historico.alterado_por.nome,
        # mesmo formato do HistoricoSituacaoSerializer: hora de São Paulo com offset (-03:00)
        "alterado_em": serializers.DateTimeField().to_representation(historico.alterado_em),
        "observacao": historico.observacao,
    }


class MudancasSituacaoApiView(RelatorioApiView):
    """GET /api/relatorios/mudancas-situacao/?dias=7 — mudanças de situação dos últimos dias, com quem mudou (critério 3)."""

    def get(self, request):
        try:
            dias = ler_dias(request.query_params.get("dias"), padrao=DIAS_MUDANCAS_PADRAO)
        except FiltroInvalido as erro:
            raise ValidationError(erro.erros)
        relatorio = mudancas_de_situacao(request.user.empresa_id, dias)
        return Response({
            "dias": relatorio["dias"],
            "desde": relatorio["desde"],
            "mudancas": [_mudanca(historico) for historico in relatorio["mudancas"]],
        })


def _parcela(parcela):
    contrato = parcela.contrato
    return {
        "id": parcela.pk,
        "contrato": contrato.pk,
        "numero": parcela.numero,
        "vencimento": parcela.vencimento,
        "valor": str(parcela.valor),
        "total_pago": str(parcela.total_pago),
        "saldo": str(parcela.saldo),
        "dias_em_atraso": parcela.dias_em_atraso,
        "cliente": contrato.cliente.nome_razao_social,
        "imovel_codigo": contrato.imovel.codigo,
        "corretor": contrato.corretor.nome,
    }


class ParcelasApiView(RelatorioApiView):
    """GET /api/relatorios/parcelas/?dias=30 — parcelas em atraso e a vencer nos próximos dias."""

    def get(self, request):
        try:
            dias = ler_dias(request.query_params.get("dias"))
        except FiltroInvalido as erro:
            raise ValidationError(erro.erros)
        relatorio = parcelas_em_atraso_e_a_vencer(request.user.empresa_id, dias)
        return Response({
            "hoje": relatorio["hoje"],
            "dias": relatorio["dias"],
            "limite": relatorio["limite"],
            "em_atraso": [_parcela(parcela) for parcela in relatorio["em_atraso"]],
            "a_vencer": [_parcela(parcela) for parcela in relatorio["a_vencer"]],
            "saldo_em_atraso": str(relatorio["saldo_em_atraso"]),
            "saldo_a_vencer": str(relatorio["saldo_a_vencer"]),
        })
