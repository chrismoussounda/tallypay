const sheet = document.getElementById("sheet");
const toast = document.getElementById("toast");

document.addEventListener("htmx:afterSwap", (event) => {
  if (event.detail.target.id !== "sheet-body") return;
  if (!sheet.open) sheet.showModal();
  const first = sheet.querySelector(
    ".error ~ input, input:not([type=hidden]):not([type=radio])",
  );
  first?.focus();
});

// A tap beside the sheet closes it.
sheet?.addEventListener("pointerdown", (event) => {
  const { left, right, top, bottom } = sheet.getBoundingClientRect();
  const { clientX: x, clientY: y } = event;
  if (x < left || x > right || y < top || y > bottom) sheet.close();
});

function failed() {
  if (!toast) return;
  toast.textContent = document.body.dataset.error;
  setTimeout(() => {
    toast.textContent = "";
  }, 6000);
}
document.addEventListener("htmx:responseError", failed);
document.addEventListener("htmx:sendError", failed);

document.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-copy]");
  if (button) {
    const value = document.querySelector(button.dataset.copy).textContent;
    await navigator.clipboard.writeText(value);
    button.textContent = button.dataset.copied;
  }
  const menu = document.querySelector(".menu[open]");
  if (menu && !menu.contains(event.target)) menu.open = false;
});

const tip = document.getElementById("tip");
document.querySelector(".chart svg")?.addEventListener("pointermove", (event) => {
  const day = event.target.closest("[data-tip]");
  tip.hidden = !day;
  if (!day) return;
  tip.textContent = day.dataset.tip;
  const frame = tip.parentElement.getBoundingClientRect();
  const half = tip.offsetWidth / 2;
  const left = event.clientX - frame.left;
  tip.style.left = `${Math.min(Math.max(left, half), frame.width - half)}px`;
});
document.querySelector(".chart svg")?.addEventListener("pointerleave", () => {
  tip.hidden = true;
});

document
  .querySelector(".tabs [aria-current]")
  ?.scrollIntoView({ block: "nearest", inline: "center" });
