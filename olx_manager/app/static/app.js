"use strict";

const canvas = document.querySelector("#screen");
const notice = document.querySelector("#notice");
const nav = document.querySelector("#navigation");
const titles = {
  all: "Pulpit", draft: "Szkice", ready_for_review: "Do zatwierdzenia", published: "Aktywne",
  sold: "Sprzedane", archived: "Archiwum", history: "Historia", settings: "Integracje", approved: "Zatwierdzone",
  publishing: "Publikowanie", submitted: "Przekazane do OLX", rejected: "Do poprawki",
  failed: "Błąd", verification_required: "Wymaga weryfikacji", mock_published: "Opublikowane w symulatorze",
  payment_required: "OLX wymaga opłaty", inactive: "Nieaktywne", ended: "Zakończone"
};
const labels = {
  title: "Tytuł", description: "Opis", condition: "Stan", category: "Kategoria", category_hint: "Doprecyzowanie kategorii",
  price_minor: "Cena (PLN)", negotiable: "Cena do negocjacji", location: "Miejscowość", delivery: "Odbiór i wysyłka",
  shipping_minor: "Koszt wysyłki (PLN)", notes: "Notatki prywatne", olx_category_id: "ID kategorii OLX",
  olx_city_id: "ID miejscowości OLX", olx_district_id: "ID dzielnicy OLX", olx_contact_name: "Nazwa kontaktu OLX",
  olx_attributes: "Parametry OLX (JSON)"
};
const delivery = {pickup: "Odbiór osobisty", shipping: "Wysyłka", both: "Odbiór i wysyłka"};
const editable = new Set(["draft", "ready_for_review", "approved", "rejected", "failed"]);
const audit = {
  draft_created: "Utworzono szkic", migrated_v0_1: "Przeniesiono z v0.1", fields_updated: "Zmieniono dane",
  active_revision_staged: "Przygotowano nową wersję aktywnej oferty", price_changed: "Zmieniono cenę szkicu", price_suggestion_saved: "Zapisano sugerowaną cenę", review_requested: "Przekazano do zatwierdzenia",
  owner_approved: "Właściciel zatwierdził", owner_rejected: "Odesłano do poprawki", approval_revoked: "Wycofano zgodę",
  media_attached: "Dodano zdjęcie", media_removed: "Usunięto zdjęcie", media_rotated: "Obrócono zdjęcie",
  media_reordered: "Zmieniono kolejność", media_copied: "Skopiowano album", draft_archived: "Zarchiwizowano",
  draft_soft_deleted: "Usunięto do archiwum", marked_sold_local: "Oznaczono jako sprzedane",
  mock_create_reserved: "Rozpoczęto symulację", mock_published: "Symulacja zakończona"
};
let session, sequence = 0, busy = false;
const formatPrice = new Intl.NumberFormat("pl-PL", {style: "currency", currency: "PLN"});
const cash = value => value == null ? "—" : formatPrice.format(value / 100);
const date = value => new Date(value * 1000).toLocaleString("pl-PL");

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined) continue;
    if (key === "class") node.className = value;
    else if (key === "click") node.addEventListener("click", value);
    else if (key in node && !key.startsWith("aria")) node[key] = value;
    else node.setAttribute(key, value);
  }
  for (const child of children.flat(Infinity)) {
    if (child !== null && child !== undefined) node.append(child);
  }
  return node;
}
const card = (...children) => el("section", {class: "card stack"}, children);
const p = (value, style = "muted") => el("p", {class: style}, String(value));
const link = (text, href, style = "") => el("a", {href, class: "button " + style}, text);
const badge = status => el("span", {class: "tag"}, titles[status] || status);
const header = (title, ...actions) => el("header", {class: "page-head"}, el("h1", {}, title), el("div", {class: "actions"}, actions));
const id = () => Array.from(crypto.getRandomValues(new Uint8Array(16)), n => n.toString(16).padStart(2, "0")).join("");
const route = () => location.hash.slice(1).split("/");
const path = item => "api/drafts/" + encodeURIComponent(item);
const photoPath = (item, photo) => path(item) + "/photos/" + encodeURIComponent(photo);
const show = (...children) => canvas.replaceChildren(...children);
const write = (url, body, method = "POST") => api(url, {method, body: JSON.stringify(body)});

function inform(text = "", error = false) {
  notice.hidden = !text;
  notice.textContent = text;
  notice.classList.toggle("error", error);
}

async function api(url, options = {}) {
  const method = options.method || "GET";
  const headers = new Headers(options.headers || {});
  if (method !== "GET") {
    headers.set("X-CSRF-Token", session.csrf);
    if (!headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  }
  const response = await fetch(url, {...options, headers, credentials: "same-origin", cache: "no-store"});
  let data;
  try { data = await response.json(); } catch { throw new Error("Nie udało się pobrać danych. Otwórz ponownie panel w HA."); }
  if (!response.ok) {
    const error = new Error(data.message || "Operacja nie powiodła się.");
    error.code = data.code;
    throw error;
  }
  return data;
}

async function run(task, button) {
  if (busy) return;
  busy = true;
  canvas.inert = true;
  canvas.setAttribute("aria-busy", "true");
  if (button) button.disabled = true;
  inform();
  try { await task(); }
  catch (error) {
    if (error.code === "REVISION_CONFLICT") await render();
    inform(error.message, true);
  } finally {
    busy = false;
    canvas.inert = false;
    canvas.removeAttribute("aria-busy");
    if (button) button.disabled = false;
  }
}

function btn(text, action, style = "") {
  return el("button", {type: "button", class: style, click: event => run(action, event.currentTarget)}, text);
}

function label(text, node, style = "") { return el("label", {class: style}, text, node); }

function parseCash(raw) {
  const value = raw.trim().replace(",", ".");
  if (value === "") return null;
  if (!/^\d{1,9}(\.\d{1,2})?$/.test(value)) throw new Error("Wpisz kwotę PLN z maksymalnie dwoma miejscami po przecinku.");
  const [whole, fraction = ""] = value.split(".");
  return Number(whole) * 100 + Number(fraction.padEnd(2, "0"));
}

async function listing(status, page) {
  const rows = await api("api/drafts?status=" + encodeURIComponent(status) + "&page=" + page);
  const list = el("div", {class: "list"}, rows.map(row => el("a", {class: "card row", href: "#edit/" + row.id},
    row.main_photo ? el("img", {src: photoPath(row.id, row.main_photo), alt: "", loading: "lazy"}) :
      el("span", {class: "placeholder"}, "Bez zdjęcia"),
    el("div", {}, el("h2", {}, row.title), p(session.categories[row.category]?.label || row.category),
      status === "all" ? badge(row.status) : null),
    el("strong", {class: "price"}, cash(row.price_minor)))));
  return [header(titles[status], link("Import z ChatGPT", "#import"), link("+ Nowe ogłoszenie", "#new", "primary")),
    rows.length ? list : card(p("W tej sekcji nie ma ogłoszeń.")),
    el("div", {class: "actions"}, page > 1 ? link("Poprzednia strona", "#" + status + "/" + (page - 1)) : null,
      rows.length === 50 ? link("Następna strona", "#" + status + "/" + (page + 1)) : null)];
}

function editor(fields) {
  const controls = {}, attributes = el("div", {class: "grid span"});
  const source = {...fields}, form = el("form", {class: "stack"});
  function input(name, options = {}) {
    let node;
    const value = source[name];
    const common = {name, id: "f-" + name};
    if (name === "category" || name === "delivery") {
      const optionsMap = name === "category" ? Object.fromEntries(Object.entries(session.categories).map(([k,v]) => [k,v.label])) : delivery;
      node = el("select", common, Object.entries(optionsMap).map(([k,v]) => el("option", {value:k, selected:k === value}, v)));
    } else if (name === "negotiable") {
      node = el("input", {...common, type:"checkbox", checked:value || false});
      controls[name] = node;
      return el("label",{class:"check"},node,labels[name]);
    } else {
      let text = value ?? "";
      if (["price_minor","shipping_minor"].includes(name) && value != null) text = (value / 100).toFixed(2);
      if (name === "olx_attributes") text = Object.keys(value || {}).length ? JSON.stringify(value, null, 2) : "";
      node = el(options.area ? "textarea" : "input", {...common, value:String(text), ...options.attrs});
    }
    controls[name] = node;
    return label(labels[name], node, options.wide ? "span" : "");
  }
  function renderAttributes() {
    const current = controls.category.value;
    const schema = {...session.categories[current].attributes};
    if (current === (fields.category || "general")) {
      for (const name of Object.keys(fields.attributes || {})) schema[name] ||= name;
    }
    attributes.replaceChildren(...Object.entries(schema).map(([key,text]) =>
      label(text, el("input", {name: "attribute-" + key, maxLength:200, value:current === fields.category ? fields.attributes?.[key] || "" : ""}))));
  }
  form.append(el("div", {class:"grid"},
    input("title", {wide:true, attrs:{required:true, maxLength:200}}), input("category"), input("condition", {attrs:{maxLength:200}}),
    input("description", {wide:true, area:true, attrs:{rows:5, maxLength:20000}}),
    input("price_minor", {attrs:{inputMode:"decimal"}}), input("negotiable"), input("location"), input("delivery"),
    input("shipping_minor", {attrs:{inputMode:"decimal"}}), input("category_hint"), attributes),
    el("details", {}, el("summary", {}, "Parametry publikacji OLX i notatki"), el("div",{class:"grid"},
      input("olx_category_id", {attrs:{inputMode:"numeric"}}), input("olx_city_id", {attrs:{inputMode:"numeric"}}),
      input("olx_district_id", {attrs:{inputMode:"numeric"}}), input("olx_contact_name"),
      input("olx_attributes", {wide:true, area:true}), input("notes", {wide:true, area:true, attrs:{maxLength:10000}}))));
  controls.category.addEventListener("change", renderAttributes);
  renderAttributes();
  return {form, get() {
    if (!form.reportValidity()) throw new Error("Sprawdź wymagane pola.");
    const fieldsData = Object.fromEntries(Object.entries(controls).map(([key,value]) => [key,value.value]));
    for (const key of ["olx_category_id","olx_city_id","olx_district_id"]) {
      const raw = fieldsData[key].trim();
      if (raw && !/^\d{1,10}$/.test(raw)) throw new Error(labels[key] + " musi być liczbą całkowitą.");
      fieldsData[key] = raw ? Number(raw) : null;
    }
    fieldsData.negotiable = controls.negotiable.checked;
    fieldsData.price_minor = parseCash(fieldsData.price_minor);
    fieldsData.shipping_minor = parseCash(fieldsData.shipping_minor);
    fieldsData.attributes = Object.fromEntries([...attributes.querySelectorAll("input")].filter(n => n.value.trim()).map(n => [n.name.slice(10),n.value]));
    try { fieldsData.olx_attributes = JSON.parse(fieldsData.olx_attributes || "{}"); }
    catch { throw new Error("Parametry OLX muszą być poprawnym obiektem JSON."); }
    return fieldsData;
  }};
}

const draftAction = (draft, name, extra = {}) => write(path(draft.id) + "/action/" + name, {expected_revision:draft.revision,...extra});

function album(draft) {
  const base = photoPath(draft.id, "");
  const file = el("input", {type:"file", multiple:true, accept:"image/jpeg,image/png,image/webp", id:"photos"});
  const inbox = el("input", {placeholder:"np. reflektor.jpg", maxLength:200});
  const managed = editable.has(draft.status);
  const items = draft.media.map((photo,index) => {
    const move = async target => {
      const ids = draft.media.map(item => item.id);
      ids.splice(target, 0, ids.splice(index,1)[0]);
      await draftAction(draft,"reorder",{photo_ids:ids});
      await render();
    };
    const action = async (name, extra = {}) => {
      await write(base + photo.id + "/" + name,{expected_revision:draft.revision,...extra});
      await render();
    };
    return el("article", {class:"photo"}, el("a", {href:base + photo.id + "?original=1", title:"Pobierz oryginał"},
      el("img", {src:base + photo.id, alt:photo.filename})),
      p(index === 0 ? "Zdjęcie główne · " + photo.filename : photo.filename),
      managed ? el("div", {class:"actions"},
        index > 0 ? btn("Główne", () => move(0)) : null,
        index > 0 ? btn("Wyżej", () => move(index - 1)) : null,
        index + 1 < draft.media.length ? btn("Niżej", () => move(index + 1)) : null,
        btn("Obróć", () => action("rotate",{rotation:(photo.rotation + 90) % 360})),
        btn("Usuń", () => action("remove"), "danger")) : null);
  });
  const uploader = async () => {
    if (!file.files.length) throw new Error("Wybierz co najmniej jedno zdjęcie.");
    let revision = draft.revision, done = 0;
    const issues = [];
    for (const image of file.files) {
      try {
        const result = await api(path(draft.id) + "/photos", {method:"POST",body:image,headers:{
          "Content-Type":image.type || "application/octet-stream", "X-Expected-Revision":String(revision),
          "X-Filename":encodeURIComponent(image.name)
        }});
        revision = result.revision;
        done++;
      } catch (error) {
        issues.push(image.name + " – " + error.message);
        if (error.code === "REVISION_CONFLICT") break;
      }
    }
    await render();
    inform("Dodane zdjęcia: " + done + (issues.length ? ". " + issues.join(" ") : "."), Boolean(issues.length));
  };
  return card(el("h2", {}, "Album zdjęć"), draft.legacy_media.length ? p("Zdjęcia z v0.1 wymagające ponownego importu: " + draft.legacy_media.join(", "), "warning") : null,
    items.length ? el("div", {class:"photos"}, items) : p("Dodaj pierwsze zdjęcie."),
    managed ? el("div", {class:"stack"}, label("Wybierz zdjęcia JPG, PNG lub WebP", file),
      el("div", {class:"actions"}, btn("Prześlij zdjęcia", uploader, "primary")),
      el("details", {}, el("summary", {}, "Import z folderu inbox"), el("div", {class:"stack"},
        label("Nazwa pliku", inbox), el("div",{class:"actions"},btn("Importuj zdjęcie", async () => {
          await draftAction(draft,"inbox",{filename:inbox.value}); await render();
        }))))) : null);
}

function historyCard(history) {
  return card(el("h2", {}, "Historia"), history.length ? el("ol",{class:"history"},
    history.map(event => el("li",{},date(event.happened_at) + " · " + (audit[event.event] || event.event) +
      (event.revision ? " · wersja " + event.revision : "") + " · " +
      (event.actor.startsWith("owner:") ? "Właściciel" : event.actor === "mcp" ? "MCP" : "System"),
      event.draft_id ? [" · ",el("a",{href:"#edit/" + event.draft_id},event.title)] : null))) : p("Brak wpisów."));
}

function publicationCard(draft) {
  const pub = draft.publication;
  const demo = pub.demo;
  const real = pub.olx;
  const current = demo && demo.revision === draft.revision;
  if (!pub.message && !demo && !real && draft.status !== "published") return null;
  return card(el("h2", {}, "Publikacja"), pub.message ? p(pub.message) : null,
    real ? p("OLX · " + (titles[real.status] || real.status) + (real.provider_status ? " · " + real.provider_status : "") +
      (real.provider_id ? " · ID " + real.provider_id : "") + (real.error ? " · " + real.error : "")) : null,
    demo?.provider_url ? el("a", {href:demo.provider_url}, current ? "Otwórz wynik demonstracyjny" : "Symulacja wcześniejszej wersji") : null,
    real?.provider_url ? el("a", {href:real.provider_url, rel:"noreferrer", target:"_blank"}, "Otwórz aktywną ofertę w OLX") : null);
}

function preview(draft, demo) {
  const f = draft.fields;
  const attributes = session.categories[f.category].attributes;
  const facts = {
    Kategoria: session.categories[f.category].label + (f.category_hint ? " · " + f.category_hint : ""),
    Stan: f.condition, Miejscowość: f.location, Dostawa: delivery[f.delivery],
    ...(f.delivery !== "pickup" ? {"Koszt wysyłki":cash(f.shipping_minor)} : {}),
    ...(f.negotiable ? {"Negocjacja":"Tak"} : {"Negocjacja":"Nie"}),
    ...Object.fromEntries(Object.entries(f.attributes).map(([key,value]) => [attributes[key] || key,value])),
    ...(f.olx_category_id ? {"Kategoria OLX":String(f.olx_category_id)} : {}),
    ...(f.olx_city_id ? {"ID miasta OLX":String(f.olx_city_id)} : {}),
    ...(f.olx_district_id ? {"ID dzielnicy OLX":String(f.olx_district_id)} : {}),
    ...(f.olx_contact_name ? {"Kontakt OLX":f.olx_contact_name} : {}),
    ...Object.fromEntries(Object.entries(f.olx_attributes).map(([key,value]) => ["OLX · " + key,value]))
  };
  const details = Object.entries(facts).flatMap(([key,value]) => [el("dt",{},key),el("dd",{},value || "—")]);
  const invoke = async (name, extra = {}) => {
    await draftAction(draft,name,extra); await render();
  };
  const controls = [];
  if (!demo) {
    if (["draft","rejected","failed"].includes(draft.status) && !draft.missing.length)
      controls.push(btn("Przekaż do zatwierdzenia", () => invoke("request"), "primary"));
    if (draft.status === "ready_for_review") controls.push(
      btn("Zatwierdź ogłoszenie", () => invoke("approve",{digest:draft.review_digest}), "primary"),
      btn("Odeślij do poprawki", () => invoke("reject")));
    if (draft.status === "approved") {
      if (!draft.approval.expired) {
        if (session.mock_enabled) controls.push(btn("Symuluj publikację", () => invoke("simulate"), "primary"));
        if (!draft.publication.message.includes("oczekuje") && draft.publication.message.includes("gotowe"))
          controls.push(btn("Opublikuj w OLX", () => invoke("publish"), "primary"));
      }
      controls.push(btn("Wycofaj zatwierdzenie", () => invoke("revoke")));
    }
    if (draft.publication.olx && ["submitted","verification_required","published","payment_required"].includes(draft.publication.olx.status))
      controls.push(btn("Sprawdź status OLX", async () => {
        await write(path(draft.id) + "/action/refresh",{}); await render();
      }));
    if (draft.publication.olx?.status === "published" && (draft.status === "published" || editable.has(draft.status))) controls.push(btn("Zakończ ofertę jako sprzedaną", async () => {
      if (confirm("Oznaczyć przedmiot jako sprzedany i dezaktywować ogłoszenie OLX?")) await invoke("end");
    },"danger"));
  }
  const suggestion = draft.price_suggestion;
  const currentDemo = draft.publication.demo?.revision === draft.revision;
  return [header(demo ? "Wynik demonstracyjny" : "Podgląd ogłoszenia", link("Wróć do szkicu", "#edit/" + draft.id)),
    demo ? p(currentDemo ? "Symulacja · oferta widoczna wyłącznie w tym panelu." :
      "Symulacja dotyczy wcześniejszej rewizji. Poniżej widzisz aktualne dane szkicu.", "warning") : null,
    el("div", {class:"preview-grid"}, card(draft.media.length ?
      el("div",{class:"gallery"},draft.media.map(photo => el("a", {
        href:photoPath(draft.id,photo.id), target:"_blank", rel:"noreferrer"},
        el("img",{src:photoPath(draft.id,photo.id),alt:photo.filename})))) : p("Brak zdjęć.")),
      card(el("div",{},badge(draft.status)),el("h2",{},f.title),el("strong",{class:"price"},cash(f.price_minor)),
        p(f.description || "Dodaj opis.", "copy"), el("dl",{},details),
        f.olx_category_id ? el("details",{},el("summary",{},"Treść wysyłana do OLX"),p(draft.olx_description,"copy")) : null)),
    suggestion ? card(el("h2",{},"Sugestia ceny"),p(cash(suggestion.price_minor) + " · " + date(suggestion.created_at) +
      (suggestion.sources.length ? " · pewność: " + ({low:"niska",medium:"średnia",high:"wysoka"}[suggestion.confidence]) : " · szacunek bez źródeł")),
      suggestion.min_minor != null || suggestion.max_minor != null ? p("Zakres: " + cash(suggestion.min_minor) + " – " + cash(suggestion.max_minor)) : null,
      suggestion.notes ? p(suggestion.notes) : null, suggestion.sources.map(value => p(value))) : null,
    publicationCard(draft),
    draft.missing.length ? p("Do uzupełnienia: " + draft.missing.join(", ") + ".", "warning") : null,
    controls.length ? card(el("div",{class:"actions"},controls)) : null].filter(Boolean);
}

async function editView(draft) {
  const controls = [link("Podgląd", "#preview/" + draft.id, "primary")];
  const panels = [];
  if (editable.has(draft.status)) {
    const edit = editor(draft.fields);
    const save = async () => {
      await write(path(draft.id),{expected_revision:draft.revision,changes:edit.get()},"PATCH");
      await render(); inform("Zapisano szkic.");
    };
    edit.form.addEventListener("submit", event => {event.preventDefault(); run(save);});
    edit.form.append(el("div",{class:"actions"},el("button",{type:"submit",class:"primary"},"Zapisz zmiany")));
    panels.push(card(el("h2",{},"Dane ogłoszenia"),edit.form));
  } else if (draft.status === "published") {
    controls.push(btn("Przygotuj zmiany oferty",async () => {
      await draftAction(draft,"stage"); await render();
    }));
    const price = el("input",{inputMode:"decimal",value:(draft.fields.price_minor / 100).toFixed(2)});
    panels.push(card(el("h2",{},"Nowa cena"),p("Cena na OLX zmieni się po zatwierdzeniu nowej wersji i publikacji."),
      label("Cena (PLN)",price),el("div",{class:"actions"},btn("Przygotuj zmianę ceny",async () => {
        const amount = parseCash(price.value);
        if (!amount) throw new Error("Wpisz dodatnią cenę.");
        await draftAction(draft,"stage_price",{changes:{price_minor:amount}});
        await render();
      }))));
  }
  if (editable.has(draft.status) || ["archived","published"].includes(draft.status)) {
    controls.push(btn("Duplikuj",async () => {
      const copy = await draftAction(draft,"duplicate",{request_id:id(),with_media:true}); location.hash="#edit/" + copy.id;
    }),btn("Użyj jako szablonu",async () => {
      const copy = await draftAction(draft,"duplicate",{request_id:id(),with_media:false}); location.hash="#edit/" + copy.id;
    }));
  }
  if (editable.has(draft.status) || ["sold","failed"].includes(draft.status))
    controls.push(btn("Do archiwum",async () => {await draftAction(draft,"archive"); location.hash="#archived";}));
  if (editable.has(draft.status))
    controls.push(btn("Oznacz jako sprzedane",async () => {
      if (confirm("Oznaczyć przedmiot jako sprzedany lokalnie?")) {await draftAction(draft,"sold"); location.hash="#sold";}
    }));
  const history = await api(path(draft.id) + "/history");
  return [header(draft.fields.title, ...controls), el("div",{class:"actions"},badge(draft.status),p("Wersja " + draft.revision)),
    ...panels, album(draft), publicationCard(draft),historyCard(history)].filter(Boolean);
}

function createView(importing) {
  const requestId = id();
  const create = async (fields, suggestion = null) => {
    const result = await write("api/drafts",{request_id:requestId,fields,suggestion});
    location.hash = "#edit/" + result.id;
  };
  if (importing) {
    const field = el("textarea",{id:"chatgpt-json",rows:11,maxLength:36000,placeholder:'{"title":"…","description":"…","price_minor":15000}'});
    return [header("Import szkicu z ChatGPT",link("Pulpit","#all")),card(
      p("Poproś ChatGPT o szkic w formacie JSON, wklej go poniżej i dodaj fotografie w albumie."),
      el("details",{},el("summary",{},"Prompt do skopiowania"),el("pre",{},
        'Na podstawie moich danych przygotuj pojedynczy obiekt JSON do importu w Universal Listings Manager. Pola: title, description, category (general/car_parts/electronics/tools/furniture/appliances), condition, location, price_minor (w groszach albo null), negotiable (boolean), delivery (pickup/shipping/both), shipping_minor (w groszach albo null), attributes (obiekt tekstowy). Jeżeli masz sugestię ceny, zwróć obiekt {fields, suggestion} z polami price_minor, min_minor, max_minor, confidence (low/medium/high), sources (URL-e) i notes. Przy braku źródeł przyjmij low lub pomiń sugestię. Nie zgaduj numeru OEM ani kompatybilności. Zwróć sam JSON.')),
      label("Dane szkicu w JSON",field),
      el("div",{class:"actions"},btn("Importuj szkic",async () => {
        let data;
        try { data = JSON.parse(field.value.trim().replace(/^```(?:json)?\s*|\s*```$/gi,"")); }
        catch { throw new Error("Wklej poprawny obiekt JSON."); }
        if (data && typeof data === "object" && !Array.isArray(data) && Object.hasOwn(data,"fields"))
          await create(data.fields,data.suggestion ?? null);
        else await create(data);
      },"primary")))];
  }
  const edit = editor({category:"general",delivery:"pickup",condition:""});
  edit.form.addEventListener("submit",event => {event.preventDefault(); run(() => create(edit.get()));});
  edit.form.append(el("div",{class:"actions"},el("button",{class:"primary",type:"submit"},"Utwórz szkic")));
  return [header("Nowe ogłoszenie",link("Pulpit","#all")),card(edit.form)];
}

async function settingsView() {
  const value = await api("api/connections");
  const {olx} = value;
  const client = el("input",{autocomplete:"off",id:"olx-client"});
  const secret = el("input",{type:"password",autocomplete:"off",id:"olx-secret"});
  const category = el("input",{inputMode:"numeric",id:"lookup-category"});
  const city = el("input",{inputMode:"numeric",id:"lookup-city"});
  const output = el("pre");
  const lookup = async (type, number) => {
    const key = number.trim();
    if (key && !/^\d{1,10}$/.test(key)) throw new Error("Wpisz liczbowe ID.");
    output.textContent = JSON.stringify(await api("api/olx/" + type + (key ? "/" + key : "")), null, 2);
  };
  return [header("Integracje"),card(el("h2",{},"ChatGPT / MCP"),
    p("Serwer MCP: " + (value.mcp_local ? "włączony i chroniony" : "wyłączony")),
    p(value.remote_mcp === "disconnected" ? "Zdalne MCP: niepołączone." : "Zdalne MCP: skonfigurowane, wymaga testu z ChatGPT."),
    link("Importuj szkic z ChatGPT","#import")),
    card(el("h2",{},"OLX Partner API"),p(olx.status === "OLX_NOT_CONFIGURED" ? "OLX_NOT_CONFIGURED · Brak powiązanego konta." : "Konto OLX połączone."),
      p("Zapis OLX: " + (olx.write_enabled ? "włączony" : "wyłączony")),
      olx.redirect_uri ? p("Adres przekierowania: " + olx.redirect_uri) : p("Do połączenia potrzebna jest zaakceptowana aplikacja OLX i skonfigurowany HTTPS callback."),
      olx.redirect_uri ? el("div",{class:"grid"},label("Client ID",client),label("Client secret",secret)) : null,
      el("div",{class:"actions"},olx.redirect_uri ? btn("Połącz z OLX",async () => {
        const popup = window.open("about:blank","_blank");
        if (popup) popup.opener = null;
        try {
          const result = await write("api/olx/start",{client_id:client.value,client_secret:secret.value});
          secret.value="";
          if (popup) popup.location.href = result.url;
          else inform("Przeglądarka zablokowała okno autoryzacji.",true);
        } catch (error) { popup?.close(); throw error; }
      },"primary") : null,
      olx.status !== "OLX_NOT_CONFIGURED" ? btn("Odłącz konto",async () => {
        if (confirm("Odłączyć konto OLX od aplikacji?")) {await write("api/olx/disconnect",{}); await render();}
      },"danger") : null),
      olx.status !== "OLX_NOT_CONFIGURED" ? el("details",{},el("summary",{},"Dane kategorii i lokalizacji z OLX"),
        el("div",{class:"stack"},label("Opcjonalne ID kategorii",category),btn("Pobierz kategorie",() => lookup("categories",category.value)),
          label("ID miejscowości",city),btn("Sprawdź miasto",() => lookup("cities",city.value)),output)) : null)];
}

async function render() {
  const current = ++sequence;
  const [view = "all",arg] = route();
  for (const node of nav.querySelectorAll("a")) {
    if (node.dataset.nav === (view || "all")) node.setAttribute("aria-current","page");
    else node.removeAttribute("aria-current");
  }
  if (!session.owner) {
    show(header("Konfiguracja właściciela"),card(session.your_user_id ?
      p("W Ustawienia → Aplikacje → Universal Listings Manager → Konfiguracja wklej poniższy identyfikator do owner_user_id. Zapisz i uruchom ponownie aplikację.") :
      p("Za dostęp do panelu odpowiada właściciel skonfigurowany w Home Assistant."),
      session.your_user_id ? el("code",{},session.your_user_id) : null));
    return;
  }
  try {
    let nodes;
    if (view === "new" || view === "import") nodes = createView(view === "import");
    else if (view === "settings") nodes = await settingsView();
    else if (view === "history") nodes = [header("Historia operacji"),historyCard(await api("api/history"))];
    else if (["edit","preview","demo"].includes(view) && arg) {
      const draft = await api(path(arg));
      nodes = view === "edit" ? await editView(draft) : preview(draft,view === "demo");
    } else {
      const status = Object.hasOwn(titles,view) ? view : "all";
      const page = /^\d{1,6}$/.test(arg || "") ? Math.max(1,Number(arg)) : 1;
      nodes = await listing(status,page);
    }
    if (sequence === current) show(...nodes);
  } catch (error) {
    if (sequence === current) show(card(p(error.message,"warning"),link("Pulpit","#all")));
  }
}

window.addEventListener("hashchange", () => {inform(); render();});
(async () => {
  try { session = await api("api/session"); await render(); }
  catch (error) { show(card(p(error.message,"warning"))); }
})();
