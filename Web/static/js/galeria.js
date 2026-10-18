// Galeria da página do imóvel: clicar numa miniatura troca a foto grande.
// Sem JavaScript, as miniaturas continuam visíveis — a página não quebra.
document.querySelectorAll("[data-galeria]").forEach(function (galeria) {
  var principal = galeria.querySelector("[data-galeria-principal]");
  var miniaturas = galeria.querySelectorAll("[data-galeria-foto]");
  if (!principal) {
    return;
  }

  miniaturas.forEach(function (miniatura, indice) {
    if (indice === 0) {
      miniatura.classList.add("ativa");
    }
    miniatura.addEventListener("click", function () {
      principal.src = miniatura.dataset.galeriaFoto;
      miniaturas.forEach(function (outra) {
        outra.classList.remove("ativa");
      });
      miniatura.classList.add("ativa");
    });
  });
});
