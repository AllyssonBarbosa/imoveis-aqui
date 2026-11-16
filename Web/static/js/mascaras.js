// Máscaras de digitação do painel.
//
// Só ajudam a digitar — quem valida de verdade é o Django. Se o JavaScript
// estiver desligado, o campo continua funcionando como texto comum.
//
// Uso: <input data-mascara="data">  →  aceita só números e põe as barras (dd/mm/aaaa).

(function () {
    "use strict";

    function formatarData(digitos) {
        digitos = digitos.slice(0, 8);
        if (digitos.length > 4) {
            return digitos.slice(0, 2) + "/" + digitos.slice(2, 4) + "/" + digitos.slice(4);
        }
        if (digitos.length > 2) {
            return digitos.slice(0, 2) + "/" + digitos.slice(2);
        }
        return digitos;
    }

    // Depois de reformatar, devolve o cursor pro mesmo ponto: conta quantos
    // números havia antes dele e acha essa posição no texto já com as barras.
    function posicaoDoCursor(textoFormatado, numerosAntes) {
        if (numerosAntes === 0) {
            return 0;
        }
        var vistos = 0;
        for (var i = 0; i < textoFormatado.length; i++) {
            if (/\d/.test(textoFormatado[i])) {
                vistos++;
                if (vistos === numerosAntes) {
                    return i + 1;
                }
            }
        }
        return textoFormatado.length;
    }

    function aplicarMascaraDeData(campo) {
        campo.setAttribute("inputmode", "numeric");
        campo.setAttribute("maxlength", "10");
        campo.setAttribute("autocomplete", "off");

        campo.addEventListener("input", function () {
            var cursor = campo.selectionStart;
            var numerosAntes = campo.value.slice(0, cursor).replace(/\D/g, "").length;
            var formatado = formatarData(campo.value.replace(/\D/g, ""));

            if (formatado !== campo.value) {
                campo.value = formatado;
                var nova = posicaoDoCursor(formatado, numerosAntes);
                campo.setSelectionRange(nova, nova);
            }
        });

        // bloqueia letras e símbolos já na tecla (o "input" acima cobre colar e autocompletar)
        campo.addEventListener("keydown", function (evento) {
            var tecla = evento.key;
            var ehAtalho = evento.ctrlKey || evento.metaKey || evento.altKey;
            if (ehAtalho || tecla.length > 1 || /\d/.test(tecla)) {
                return; // Backspace, setas, Tab, Ctrl+V etc. passam
            }
            evento.preventDefault();
        });

        // valor que já veio do servidor (ex.: formulário reexibido com erro)
        if (campo.value) {
            campo.value = formatarData(campo.value.replace(/\D/g, ""));
        }
    }

    document.addEventListener("DOMContentLoaded", function () {
        document.querySelectorAll('input[data-mascara="data"]').forEach(aplicarMascaraDeData);
    });
})();
