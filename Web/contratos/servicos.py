"""Regras do contrato (W12) — o painel e a API só chamam daqui.

- contratos_visiveis_para(): quem enxerga qual contrato.
- salvar_contrato(): grava o contrato e gera (ou gera de novo) as parcelas.
"""

from django.core.exceptions import ValidationError
from django.db import transaction

from contas.models import Usuario

from .calculos import valores_das_parcelas, vencimentos
from .models import Contrato, Parcela


def contratos_visiveis_para(usuario):
    """Sempre filtra pela empresa do usuário e depois pelo perfil.

    - gestor: todos os contratos da empresa
    - corretor: só os contratos em que ele é o corretor (só leitura)
    - administrador (sem empresa): nenhum
    """
    if not usuario.is_authenticated or usuario.empresa_id is None:
        return Contrato.objects.none()
    contratos = Contrato.objects.filter(empresa_id=usuario.empresa_id)
    if usuario.perfil == Usuario.Perfil.GESTOR:
        return contratos
    if usuario.perfil == Usuario.Perfil.CORRETOR:
        return contratos.filter(corretor=usuario)
    return contratos.none()


def calcular_parcelas(contrato):
    """Lista de (número, vencimento, valor) que o contrato deve ter.

    Venda: (valor − entrada) dividido em N, centavos que sobram na última.
    Locação: N aluguéis iguais, N = meses da vigência (calculado no clean()).
    """
    quantidade = contrato.numero_parcelas
    if contrato.tipo == Contrato.Tipo.LOCACAO:
        valores = [contrato.valor] * quantidade
    else:
        valores = valores_das_parcelas(contrato.valor - contrato.entrada, quantidade)
    datas = vencimentos(contrato.data_inicio, quantidade, contrato.dia_vencimento)
    return [(numero, data, valor) for numero, (data, valor) in enumerate(zip(datas, valores), start=1)]


def _gerar_parcelas(contrato):
    contrato.parcelas.all().delete()
    Parcela.objects.bulk_create([
        Parcela(empresa_id=contrato.empresa_id, contrato=contrato, numero=numero, vencimento=data, valor=valor)
        for numero, data, valor in calcular_parcelas(contrato)
    ])


def _campos_alterados(contrato, gravado):
    """Campos do negócio que estão diferentes do que está gravado no banco."""
    nomes = [Contrato._meta.get_field(campo).attname for campo in Contrato.CAMPOS_DO_NEGOCIO]
    return [
        campo for campo, nome in zip(Contrato.CAMPOS_DO_NEGOCIO, nomes)
        if getattr(gravado, nome) != getattr(contrato, nome)
    ]


@transaction.atomic
def salvar_contrato(contrato, usuario):
    """Grava o contrato. Ninguém digita parcela: elas saem daqui.

    - contrato novo: grava e gera todas as parcelas;
    - rascunho editado: se mudou valor, entrada, nº de parcelas, dia ou
      datas, apaga as parcelas e gera de novo; se mudou só observação
      (ou imóvel/cliente/corretor), as parcelas ficam como estão;
    - assinado: só observações podem mudar — o resto é recusado.

    `contrato.empresa` precisa estar definida (sempre a do usuário logado).
    Tudo dentro de uma transação: se algo falhar, nada fica pela metade.
    """
    novo = contrato.pk is None
    gravado = None
    if novo:
        contrato.criado_por = usuario
    else:
        # select_for_update: trava a linha até o fim da transação (duas edições ao mesmo tempo
        # não geram parcelas misturadas)
        gravado = Contrato.objects.select_for_update().get(pk=contrato.pk)
        if not gravado.pode_alterar_negocio():
            # na locação o nº de parcelas é calculado, não digitado — não conta como mudança
            calculado = {"numero_parcelas"} if gravado.tipo == Contrato.Tipo.LOCACAO else set()
            if set(_campos_alterados(contrato, gravado)) - calculado:
                raise ValidationError(
                    "Contrato assinado: valores, datas, imóvel, cliente e corretor não podem mais "
                    "ser alterados. Só as observações."
                )

    # o clean() também calcula o nº de parcelas da locação, então vem antes da comparação abaixo
    contrato.full_clean()
    alterados = _campos_alterados(contrato, gravado) if gravado else []

    contrato.save()
    if novo or any(campo in Contrato.CAMPOS_DAS_PARCELAS for campo in alterados):
        _gerar_parcelas(contrato)
    return contrato
