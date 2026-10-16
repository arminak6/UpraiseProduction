/* Shared, local presentation helpers. All external text must pass through escape. */
(() => {
  const paths = {
    upload: '<path d="M12 16V4m-4 4 4-4 4 4M4 16v4h16v-4"/>',
    document: '<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M14 3v6h6M8 13h8M8 17h5"/>',
    book: '<path d="M12 5c-3-2-6-2-10-1v15c4-1 7-1 10 1 3-2 6-2 10-1V4c-4-1-7-1-10 1zm0 0v15"/>',
    sparkles: '<path d="m12 3 2.3 6.7L21 12l-6.7 2.3L12 21l-2.3-6.7L3 12l6.7-2.3L12 3zM20 2v4m-2-2h4M3 19v4m-2-2h4"/>',
    arrow: '<path d="M4 12h15m-6-6 6 6-6 6"/>',
    back: '<path d="M20 12H5m6-6-6 6 6 6"/>',
    check: '<path d="m5 12 4 4L19 6"/>',
    checkCircle: '<circle cx="12" cy="12" r="9"/><path d="m8 12 3 3 5-6"/>',
    clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    globe: '<circle cx="12" cy="12" r="9"/><ellipse cx="12" cy="12" rx="4" ry="9"/><path d="M3 12h18"/>',
    lock: '<rect x="5" y="10" width="14" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3m-4 4v3"/>',
    info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-10v1"/>',
    search: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
    trash: '<path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7m4-7v7"/>',
    close: '<path d="m6 6 12 12M6 18 18 6"/>',
    layers: '<path d="m12 3 10 5-10 5L2 8l10-5zm-9 9 9 5 9-5m-18 5 9 5 9-5"/>',
    play: '<path d="m9 5 11 7-11 7z" fill="currentColor" stroke-width="1"/>',
    pause: '<path d="M8 5h2v14H8zm6 0h2v14h-2z" fill="currentColor" stroke-width="1"/>',
    stop: '<rect x="5" y="5" width="14" height="14" rx="1" fill="currentColor" stroke-width="1"/>',
    replay: '<path d="M3 10a9 9 0 1 1 1 7M3 4v6h6"/>',
    chat: '<path d="M21 11a8 8 0 0 1-8 8H7l-5 3V11a9 9 0 0 1 19 0z"/><path d="M7 10h9M7 14h5"/>',
    send: '<path d="m22 2-7 20-4-9-9-4 20-7zm0 0L11 13"/>',
    headphones: '<path d="M4 14v-3a8 8 0 0 1 16 0v3"/><rect x="3" y="12" width="4" height="8" rx="2"/><rect x="17" y="12" width="4" height="8" rx="2"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    lightbulb: '<path d="M9 18v-1a7 7 0 1 1 6 0v1M9 21h6M9 18h6"/>',
    leaf: '<path d="M20 3C7 2 2 8 5 15c4 9 16 2 15-12zM5 20 16 8"/>',
    loader: '<path d="M21 12a9 9 0 1 1-6-8.5"/>',
    edit: '<path d="m16 3 5 5-12 12-6 1 1-6L16 3zm-2 2 5 5"/>',
    eye: '<path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
  };
  function icon(name, className = "") {
    return `<svg class="icon ${className}" viewBox="0 0 24 24" aria-hidden="true">${paths[name] || paths.info}</svg>`;
  }
  function escape(value) {
    return String(value ?? "").replace(/[&<>"']/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));
  }
  function art(theme = "teal", index = "01") {
    return `<div class="course-art ${theme === "sand" ? "sand" : "teal"}"><span class="art-label">A new way to understand</span>
      <svg class="book-art" viewBox="0 0 120 110" fill="none" aria-hidden="true"><ellipse cx="60" cy="94" rx="41" ry="5" fill="currentColor" opacity=".08"/>
        <path d="M20 28c16-5 27 0 40 8 13-8 24-13 40-8v51c-16-4-27 1-40 9-13-8-24-13-40-9V28z" fill="white" fill-opacity=".65" stroke="currentColor" stroke-opacity=".4" stroke-width="1.5"/>
        <path d="M60 36v52M28 41c8-1 16 2 24 7m-24 3c8-1 16 2 24 7m-24 3c8-1 16 2 24 7m16-20c8-5 16-8 24-7m-24 17c8-5 16-8 24-7m-24 17c8-5 16-8 24-7" stroke="currentColor" stroke-opacity=".5" stroke-width="1.6" stroke-linecap="round"/>
        <path d="m87 10 2 6 6 2-6 2-2 6-2-6-6-2 6-2 2-6z" fill="currentColor" opacity=".7"/><circle cx="28" cy="15" r="3" stroke="currentColor" stroke-opacity=".5"/>
        <path d="M106 47h6m-3-3v6" stroke="currentColor" stroke-opacity=".5"/></svg><span class="art-number">UPRAISE / ${escape(index)}</span></div>`;
  }
  window.UpraiseUI = Object.freeze({ icon, escape, art });
})();
