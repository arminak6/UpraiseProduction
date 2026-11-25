"use strict";

const byId = id => document.getElementById(id);
const error = byId("error");
const login = byId("login");
let sessionView = 0;

async function request(path, data) {
  const options = data === undefined ? {} : {
    method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(data),
  };
  const response = await fetch(`/demo/${path}`, {...options, cache: "no-store", credentials: "same-origin"});
  return {status: response.status, ok: response.ok, data: await response.json()};
}

function showUser(user) {
  sessionView += 1;
  byId("sign-in").hidden = Boolean(user);
  byId("workspace").hidden = !user;
  byId("identity").textContent = user ? `Signed in as ${user}.` : "";
  byId("result").hidden = true;
  byId("result-body").textContent = "";
}

async function act(button, action) {
  button.disabled = true;
  error.textContent = "";
  try {
    await action();
  } catch {
    error.textContent = "The demo could not be reached. Check that it is running and try again.";
  } finally {
    button.disabled = false;
  }
}

login.addEventListener("submit", event => {
  event.preventDefault();
  void act(login.querySelector("button"), async () => {
    const {ok, status, data} = await request("login", Object.fromEntries(new FormData(login)));
    byId("password").value = "";
    if (ok) {
      if (data.workspace === "/") { window.location.replace("/"); return; }
      login.reset();
      showUser(data.user);
      byId("documents").focus();
    } else {
      error.textContent = status === 401 ? "Incorrect demo username or password." : "Sign-in failed. Try again.";
      byId("password").focus();
    }
  });
});

byId("logout").addEventListener("click", event => {
  void act(event.currentTarget, async () => {
    const {ok} = await request("logout", {});
    if (!ok) throw new Error("Sign-out failed");
    showUser(null);
    byId("username").focus();
  });
});

for (const endpoint of ["documents", "version"]) {
  byId(endpoint).addEventListener("click", event => {
    void act(event.currentTarget, async () => {
      const currentView = sessionView;
      const {status, data} = await request(endpoint);
      if (currentView !== sessionView) return;
      if (data.detail === "DEMO_LOGIN_REQUIRED") {
        showUser(null);
        error.textContent = "Your demo session expired. Sign in again.";
        return;
      }
      byId("result-status").textContent = `${data.endpoint || "Demo middleware"} returned HTTP ${status}`;
      byId("result-body").textContent = JSON.stringify(data.body ?? data, null, 2);
      byId("result").hidden = false;
    });
  });
}

request("session").then(({data}) => {
  if (data.user && data.workspace === "/") window.location.replace("/");
  else showUser(data.user);
}).catch(() => {
  error.textContent = "The demo could not be reached. Check that it is running and reload the page.";
});
