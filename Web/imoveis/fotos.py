"""Regras das fotos do imóvel, num lugar só (APP 11).

As rotas aninhadas `/api/imoveis/<id>/fotos/...` só chamam estas funções. Cada função recebe o
imóvel já recortado pela empresa do usuário (quem chama garante o isolamento) e roda numa
transação que trava a linha do imóvel (`select_for_update`): dois envios ao mesmo tempo não
repetem a ordem, não criam duas capas e não furam o teto de fotos.

Erros de regra saem como `ValidationError` com a chave do campo (`imagem` ou `ids`) e a mensagem
em português, para a view devolver 400 sem traduzir nada.
"""

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max

from .models import FotoImovel, Imovel

LIMITE_FOTOS_POR_IMOVEL = 30
MENSAGEM_LIMITE = f"Este imóvel já tem {LIMITE_FOTOS_POR_IMOVEL} fotos, o máximo permitido."
MENSAGEM_IDS_FORMATO = "Envie a lista de ids das fotos (números inteiros)."
MENSAGEM_IDS_LISTA = "Envie a lista completa das fotos deste imóvel, sem repetir."


def _travar_imovel(imovel_id):
    """Trava a linha do imóvel até o fim da transação (no SQLite é ignorado, sem erro)."""
    Imovel.objects.select_for_update().filter(pk=imovel_id).values_list("pk", flat=True).get()


def fotos_do_imovel(imovel):
    """Fotos na ordem que o corretor arrumou; a capa é marcada pelo campo, não pela posição."""
    return FotoImovel.objects.filter(imovel=imovel).order_by("ordem", "id")


def adicionar_foto(imovel, arquivo):
    """Grava UMA foto no fim da ordem (maior ordem + 1). Vira capa se o imóvel ainda não tem capa.

    Valida tipo e tamanho pelo `full_clean` (`validar_imagem`) antes de gravar: se recusar, nada é
    gravado. Com o teto atingido, recusa sem olhar o arquivo.
    """
    with transaction.atomic():
        _travar_imovel(imovel.pk)
        fotos = FotoImovel.objects.filter(imovel=imovel)
        if fotos.count() >= LIMITE_FOTOS_POR_IMOVEL:
            raise ValidationError({"imagem": [MENSAGEM_LIMITE]})
        maior_ordem = fotos.aggregate(maior=Max("ordem"))["maior"]
        foto = FotoImovel(
            imovel=imovel,
            imagem=arquivo,
            ordem=0 if maior_ordem is None else maior_ordem + 1,
            capa=not fotos.filter(capa=True).exists(),
        )
        foto.full_clean()
        foto.save()
        return foto


def reordenar_fotos(imovel, ids):
    """Grava a ordem `0..n-1` na sequência de `ids`, que precisa ter todas as fotos do imóvel.

    Id faltando, repetido ou de outro imóvel recusa a lista inteira, sem mudar nada.
    """
    if not isinstance(ids, list) or any(type(item) is not int for item in ids):
        raise ValidationError({"ids": [MENSAGEM_IDS_FORMATO]})
    with transaction.atomic():
        _travar_imovel(imovel.pk)
        fotos = list(FotoImovel.objects.filter(imovel=imovel))
        if len(ids) != len(set(ids)) or set(ids) != {foto.pk for foto in fotos}:
            raise ValidationError({"ids": [MENSAGEM_IDS_LISTA]})
        por_id = {foto.pk: foto for foto in fotos}
        for posicao, foto_id in enumerate(ids):
            por_id[foto_id].ordem = posicao
        FotoImovel.objects.bulk_update(fotos, ["ordem"])


def definir_capa(foto):
    """A foto vira a única capa do imóvel."""
    with transaction.atomic():
        _travar_imovel(foto.imovel_id)
        foto.definir_como_capa()


def apagar_foto(foto):
    """Apaga a foto, fecha o buraco na ordem e, se o imóvel ficou sem capa, promove a primeira.

    O arquivo só sai do disco depois do commit: se a transação desfizer, a foto e o arquivo ficam.
    """
    with transaction.atomic():
        _travar_imovel(foto.imovel_id)
        imovel_id = foto.imovel_id
        nome_arquivo = foto.imagem.name
        armazenamento = foto.imagem.storage
        foto.delete()

        restantes = list(FotoImovel.objects.filter(imovel_id=imovel_id).order_by("ordem", "id"))
        for posicao, restante in enumerate(restantes):
            restante.ordem = posicao
        FotoImovel.objects.bulk_update(restantes, ["ordem"])
        if restantes and not any(restante.capa for restante in restantes):
            restantes[0].capa = True
            restantes[0].save(update_fields=["capa"])

        if nome_arquivo:
            transaction.on_commit(lambda: armazenamento.delete(nome_arquivo))
