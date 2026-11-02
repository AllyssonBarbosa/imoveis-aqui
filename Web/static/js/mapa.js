// Desenha o mapa do imóvel com Leaflet + OpenStreetMap (gratuito, sem chave de API).
// A coordenada vem do HTML: <div data-mapa data-latitude="..." data-longitude="...">.
document.querySelectorAll("[data-mapa]").forEach(function (elemento) {
  var latitude = parseFloat(elemento.dataset.latitude);
  var longitude = parseFloat(elemento.dataset.longitude);
  if (!window.L || isNaN(latitude) || isNaN(longitude)) {
    return;
  }

  var mapa = L.map(elemento).setView([latitude, longitude], 16);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    // O Django manda "Referrer-Policy: same-origin" no site todo, e aí os pedidos dos tiles
    // saem sem Referer — o OpenStreetMap bloqueia (403). Só nas imagens dos tiles,
    // enviamos a origem do site (sem caminho nem parâmetros), como a política de uso do OSM pede.
    referrerPolicy: "strict-origin-when-cross-origin",
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
  }).addTo(mapa);
  L.marker([latitude, longitude]).addTo(mapa);
});
