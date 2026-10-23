import { api, setCsrf } from "./api.js";

const content = document.querySelector("#content");
const message = document.querySelector("#message");
let user;
let bookables;
let dateFormat;
let timeFormat;
let dayFormat;
// Tab labels format calendar dates (YYYY-MM-DD) anchored at noon UTC.
const tabDateFormat = new Intl.DateTimeFormat(undefined, { timeZone: "UTC", weekday: "short", day: "numeric", month: "short" });
const dateOnlyFormat = new Intl.DateTimeFormat(undefined, { timeZone: "UTC", dateStyle: "full" });
const timeZone = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";

function el(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function bookingCount(count) {
  return `${count} ${count === 1 ? "booking" : "bookings"}`;
}

function appendMarkdownInline(parent, text) {
  const pattern = /(\[([^\]]+)\]\(([^)\s]+)\)|\*\*(.+?)\*\*|__(.+?)__|~~(.+?)~~|\*([^*\n]+)\*|_([^_\n]+)_|`([^`]+)`| {2}\n|\n)/g;
  let cursor = 0;
  for (const match of text.matchAll(pattern)) {
    parent.append(document.createTextNode(text.slice(cursor, match.index)));
    const [token, , label, href, strongA, strongB, strike, italicA, italicB, code] = match;
    let node;
    if (label !== undefined) {
      node = el("a", label);
      try {
        const url = new URL(href, window.location.href);
        if (!["http:", "https:", "mailto:"].includes(url.protocol)) {
          node = el("span", token);
        } else {
          node.href = href;
          if (url.origin !== window.location.origin) {
            node.target = "_blank";
            node.rel = "noopener noreferrer";
          }
        }
      } catch {
        node = el("span", token);
      }
    } else if (strongA !== undefined || strongB !== undefined) {
      node = el("strong");
      appendMarkdownInline(node, strongA ?? strongB);
    } else if (strike !== undefined) {
      node = el("del");
      appendMarkdownInline(node, strike);
    } else if (italicA !== undefined || italicB !== undefined) {
      node = el("em");
      appendMarkdownInline(node, italicA ?? italicB);
    } else if (code !== undefined) {
      node = el("code", code);
    } else {
      node = el("br");
    }
    parent.append(node);
    cursor = match.index + token.length;
  }
  parent.append(document.createTextNode(text.slice(cursor)));
}

function markdownDescription(text) {
  const root = el("div", undefined, "description");
  const lines = text.replace(/\r\n?/g, "\n").split("\n");
  let index = 0;
  while (index < lines.length) {
    const line = lines[index];
    if (!line.trim()) {
      index += 1;
      continue;
    }
    if (/^```/.test(line)) {
      const code = [];
      index += 1;
      while (index < lines.length && !/^```/.test(lines[index])) code.push(lines[index++]);
      if (index < lines.length) index += 1;
      const pre = el("pre");
      pre.append(el("code", code.join("\n")));
      root.append(pre);
      continue;
    }
    const heading = /^(#{1,6})\s+(.+)$/.exec(line);
    if (heading) {
      const node = el(`h${Math.min(heading[1].length + 2, 6)}`);
      appendMarkdownInline(node, heading[2]);
      root.append(node);
      index += 1;
      continue;
    }
    if (/^\s*(?:[-*_]\s*){3,}$/.test(line)) {
      root.append(el("hr"));
      index += 1;
      continue;
    }
    if (/^\s*>/.test(line)) {
      const quote = [];
      while (index < lines.length && /^\s*>/.test(lines[index])) {
        quote.push(lines[index++].replace(/^\s*>\s?/, ""));
      }
      const blockquote = el("blockquote", undefined, "description__quote");
      const quotedContent = markdownDescription(quote.join("\n"));
      while (quotedContent.firstChild) blockquote.append(quotedContent.firstChild);
      root.append(blockquote);
      continue;
    }
    const listMatch = /^\s*([-+*]|\d+[.)])\s+(.+)$/.exec(line);
    if (listMatch) {
      const ordered = /^\d/.test(listMatch[1]);
      const list = el(ordered ? "ol" : "ul");
      while (index < lines.length) {
        const item = /^\s*([-+*]|\d+[.)])\s+(.+)$/.exec(lines[index]);
        if (!item || /^\d/.test(item[1]) !== ordered) break;
        const entry = el("li");
        appendMarkdownInline(entry, item[2]);
        list.append(entry);
        index += 1;
      }
      root.append(list);
      continue;
    }
    const paragraph = [line.trim()];
    index += 1;
    while (index < lines.length && lines[index].trim()
      && !/^(?:#{1,6}\s|```|\s*>|\s*(?:[-+*]|\d+[.)])\s+)/.test(lines[index])
      && !/^\s*(?:[-*_]\s*){3,}$/.test(lines[index])) {
      paragraph.push(lines[index++].trim());
    }
    const node = el("p");
    appendMarkdownInline(node, paragraph.join("\n"));
    root.append(node);
  }
  return root;
}

function githubLink(login) {
  const link = el("a", `@${login}`);
  link.href = `https://github.com/${encodeURIComponent(login)}`;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  link.title = `@${login} on GitHub`;
  return link;
}

function withLogin(text, login, tag = "span") {
  const node = el(tag, text);
  node.append(githubLink(login));
  return node;
}

// Messages float in a fixed bubble so showing one never scrolls the page.
let messageTimer;
function dismissMessage() {
  clearTimeout(messageTimer);
  message.replaceChildren();
  message.className = "toast";
}

function notify(text, error = false) {
  clearTimeout(messageTimer);
  const body = el("p", text, "toast__text");
  const close = el("button", "×", "toast__close");
  close.type = "button";
  close.setAttribute("aria-label", "Dismiss message");
  close.addEventListener("click", dismissMessage);
  message.replaceChildren(body, close);
  message.className = `toast ${error ? "toast--error" : "toast--success"}`;
  message.setAttribute("role", error ? "alert" : "status");
  if (!error) messageTimer = setTimeout(dismissMessage, 5000);
}

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && message.childElementCount) dismissMessage();
});

function handleError(error) {
  notify(error instanceof Error ? error.message : String(error), true);
}

async function busy(control, operation) {
  control.disabled = true;
  try {
    await operation();
  } catch (error) {
    handleError(error);
  } finally {
    control.disabled = false;
  }
}

function button(text, operation, className = "p-button") {
  const node = el("button", text, className);
  node.type = "button";
  node.addEventListener("click", () => busy(node, operation));
  return node;
}

function field(form, name) {
  return form.elements.namedItem(name);
}

function future(item) {
  return !item.expired && new Date(item.starts_at) > new Date();
}

function overlaps(left, right) {
  const leftStart = new Date(left.starts_at).getTime();
  const rightStart = new Date(right.starts_at).getTime();
  return leftStart < rightStart + right.duration_minutes * 60000
    && rightStart < leftStart + left.duration_minutes * 60000;
}

const BOOKABLE_LABELS = { restaurant: "Restaurant", excursion: "Excursion", sports: "Sports" };

function bookingType(booking) {
  return BOOKABLE_LABELS[booking.bookable_type];
}

function bookableSubtitle(booking) {
  return `${bookingType(booking)} · ${booking.area || "Area not specified"}`;
}

function bookingTimeRange(booking) {
  const start = new Date(booking.starts_at);
  const end = new Date(start.getTime() + booking.duration_minutes * 60000);
  // Show only the time when the booking ends on the day it starts (in the viewer's zone).
  const sameDay = dayFormat.format(start) === dayFormat.format(end);
  return `${timeFormat.format(start)} - ${(sameDay ? timeFormat : dateFormat).format(end)}`;
}

// Re-render a view when its earliest shown booking starts (setTimeout caps at ~24.8 days).
let expiryTimer;
function expireAt(items, callback) {
  clearTimeout(expiryTimer);
  const next = Math.min(...items.filter(future).map((item) => new Date(item.starts_at).getTime()));
  if (!Number.isFinite(next)) return;
  const delay = Math.min(next - Date.now() + 1000, 2 ** 31 - 1);
  expiryTimer = setTimeout(() => (Date.now() >= next ? callback() : expireAt(items, callback)), delay);
}

// Avoid discarding input: wait until no edit/request form is open before re-rendering.
function renderWhenIdle() {
  if (content.querySelector("details[open] form")) {
    expiryTimer = setTimeout(renderWhenIdle, 30000);
  } else {
    render();
  }
}

function wallTime(timestamp) {
  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone,
    year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", hourCycle: "h23",
  }).formatToParts(new Date(timestamp));
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${values.year}-${values.month}-${values.day}T${values.hour}:${values.minute}`;
}

function bookingCard(
  booking,
  requestedSeats = 0,
  { grouped = false, hasRequests = requestedSeats > 0, ownerStatus } = {},
) {
  const card = el("article", undefined, "booking-table");
  card.id = `booking-${booking.id}`;
  card.dataset.bookingId = String(booking.id);
  const mine = booking.owner_login === user.login;
  const active = future(booking);
  const heading = el(grouped ? "h3" : "h2", undefined,
    grouped ? "p-heading--5" : "p-heading--3");
  const permalink = el("a",
    grouped ? bookingTimeRange(booking) : booking.bookable_name,
    "booking-permalink");
  permalink.href = `#${card.id}`;
  permalink.title = "Link to this booking";
  heading.append(permalink);
  if (mine || hasRequests) {
    const state = active && (mine || requestedSeats > 0) ? (mine ? "mine" : "requested") : "inactive";
    if (state !== "inactive") card.classList.add(`booking-table--${state}`);
    const label = mine ? "Your booking" : requestedSeats > 0 ? "Requested by you" : "Cancelled requests";
    heading.append(" ", el("span", label, `status-tag status-tag--${state}`));
  }
  if (!grouped) {
    card.classList.add("booking-summary");
  }
  card.append(heading);
  if (!grouped) {
    card.append(
      el("p", bookableSubtitle(booking)),
      el("p", `${dateFormat.format(new Date(booking.starts_at))} · ${bookingTimeRange(booking)}`),
    );
  }
  const states = el("div", undefined, "booking-states");
  if (!active) {
    card.classList.add("expired-card");
    states.append(el("span", "Expired", "badge"));
  }
  if (booking.closed) states.append(el("span", "Closed", "badge"));
  if (booking.seats_available === 0) states.append(el("span", "Full", "badge"));
  if (ownerStatus) states.append(el("span", ownerStatus, "badge"));
  if (states.childElementCount) card.append(states);
  card.append(
    seatingDiagram(booking, requestedSeats),
    withLogin("Hosted by ", booking.owner_login, "p"),
  );
  if (booking.description) {
    card.append(markdownDescription(booking.description), el("hr", undefined, "description-divider"));
  }
  return card;
}

function seatLegend() {
  const legend = el("div", undefined, "seat-legend");
  const entries = [
    ["+", "seat seat--available", "Free"],
    ["", "seat seat--occupied", "Taken"],
  ];
  if (user.login) entries.push(
    ["✓", "seat seat--requested", "Your spot(s)"],
    ["", "legend-swatch legend-swatch--mine", "Your booking"],
  );
  for (const [text, className, label] of entries) {
    const entry = el("span", undefined, "seat-legend__entry");
    const symbol = el("span", text, className);
    symbol.setAttribute("aria-hidden", "true");
    entry.append(symbol, label);
    legend.append(entry);
  }
  return legend;
}

const SEAT_TITLES = { occupied: "Taken", requested: "Your spot", available: "Free" };

function seatingDiagram(booking, requestedSeats = 0) {
  const figure = el("figure", undefined, "seating-diagram");
  const diagram = el("div", undefined, "seating-layout");
  const spareSeats = booking.seats_available + requestedSeats;
  const occupiedSeats = booking.party_size - spareSeats;
  const sports = booking.bookable_type === "sports";
  figure.setAttribute("aria-label",
    `${occupiedSeats} spots occupied by the booking or others' requests, ${requestedSeats} spots occupied by your request, ${booking.seats_available} available`);
  const top = el("div", undefined, "seat-row");
  const bottom = el("div", undefined, "seat-row");
  top.setAttribute("aria-hidden", "true");
  bottom.setAttribute("aria-hidden", "true");
  const availablePositions = new Set();
  if (sports && booking.seats_available > 0) {
    for (const half of [0, 1]) {
      const halfSize = Math.ceil((booking.party_size - half) / 2);
      const availableInHalf = half === 0
        ? Math.ceil(booking.seats_available / 2)
        : Math.floor(booking.seats_available / 2);
      for (let index = 0; index < availableInHalf; index++) {
        const position = Math.floor((index + 0.5) * halfSize / availableInHalf);
        availablePositions.add(position * 2 + half);
      }
    }
  }
  let requestedRemaining = requestedSeats;
  const sportsSeats = [];
  // Every spot is drawn; court free spots are distributed across both halves.
  for (let index = 0; index < booking.party_size; index++) {
    let state;
    if (sports) {
      if (availablePositions.has(index)) {
        state = "available";
      } else if (requestedRemaining > 0) {
        state = "requested";
        requestedRemaining--;
      } else {
        state = "occupied";
      }
      const seat = el("span", state === "requested" ? "✓" : state === "available" ? "+" : "", `seat seat--${state}`);
      seat.title = SEAT_TITLES[state];
      sportsSeats.push({ seat, index, state });
    } else {
      state = index < occupiedSeats ? "occupied"
        : index < occupiedSeats + requestedSeats ? "requested" : "available";
      const seat = el("span", state === "requested" ? "✓" : state === "available" ? "+" : "", `seat seat--${state}`);
      seat.title = SEAT_TITLES[state];
      (index % 2 === 0 ? top : bottom).append(seat);
    }
  }
  const excursion = booking.bookable_type === "excursion";
  const table = el("div", undefined, sports ? "court-caption" : excursion ? "bus-aisle" : "dining-table");
  table.append(
    el("strong", booking.name),
    el("span", `${booking.seats_available} of ${booking.party_size} available`),
  );
  if (requestedSeats > 0) table.append(el("span", `You requested ${requestedSeats} spot(s)`, "dining-table__requested"));
  if (sports) {
    const court = el("div", undefined, "court");
    court.setAttribute("aria-hidden", "true");
    const halves = [el("div", undefined, "court__half"), el("div", undefined, "court__half")];
    court.append(...halves);
    for (const { seat, index } of sportsSeats) {
      const halfIndex = index % 2;
      const halfSize = Math.ceil((booking.party_size - halfIndex) / 2);
      const positionInHalf = Math.floor(index / 2);
      const angle = ((positionInHalf / halfSize) * Math.PI * 2) - (Math.PI / 2);
      const radius = 34;
      const left = 50 + Math.cos(angle) * radius;
      const topOffset = 50 + Math.sin(angle) * radius;
      seat.style.left = `${left}%`;
      seat.style.top = `${topOffset}%`;
      halves[halfIndex].append(seat);
    }
    diagram.append(court, table);
  } else if (excursion) {
    // Excursions are drawn as a bus seen from above: window spots either side of the
    // aisle, with the engine compartment at the front (right).
    const bus = el("div", undefined, "bus");
    const engine = el("div", undefined, "bus__engine");
    engine.setAttribute("aria-hidden", "true");
    const body = el("div", undefined, "bus__body");
    for (const position of ["front-left", "front-right", "rear-left", "rear-right"]) {
      const wheel = el("span", undefined, `bus__wheel bus__wheel--${position}`);
      wheel.setAttribute("aria-hidden", "true");
      body.append(wheel);
    }
    body.append(top, table, bottom);
    bus.append(engine, body);
    diagram.classList.add("seating-layout--bus");
    diagram.append(bus);
  } else {
    diagram.append(top, table, bottom);
  }
  figure.append(diagram);
  return figure;
}

function requestUnavailable(booking, { activeRequests, requestedSeats, ownedBookings }) {
  if (!user.login) {
    return { reason: "Sign in to request spots.", label: "Sign in with GitHub", href: "/auth/login" };
  }
  if (user.login === booking.owner_login) {
    return { reason: "You cannot request spots on your own booking.", label: "Manage my spots", href: "/mine" };
  }
  if (!future(booking)) return { reason: "This booking has expired." };
  if (booking.closed) return { reason: "This booking is closed." };
  if (booking.seats_available < 1) return { reason: "No spare spots remain on this booking." };
  if (ownedBookings.some((owned) => overlaps(booking, owned))) {
    return { reason: "This overlaps a booking you own.", label: "Manage my spots", href: "/mine" };
  }
  if (requestedSeats.has(booking.id)) {
    return { reason: "You already have an active request on this booking.", label: "View my spots", href: "/mine" };
  }
  if (activeRequests.some((request) => overlaps(booking, request))) {
    return { reason: "This overlaps an active spot request you have.", label: "View my spots", href: "/mine" };
  }
  return null;
}

function bookableAreas(bookings, context, renderCard = browseCard) {
  const areas = new Map();
  for (const booking of bookings) {
    let area = areas.get(booking.bookable_id);
    if (!area) {
      const section = el("section", undefined, "bookable-area");
      const day = wallTime(booking.starts_at).split("T")[0];
      section.id = `bookable-${booking.bookable_id}-${day}`;
      section.dataset.bookableId = String(booking.bookable_id);
      const heading = el("h2", undefined, "p-heading--3");
      heading.id = `${section.id}-heading`;
      const permalink = el("a", booking.bookable_name, "bookable-permalink");
      permalink.href = `#${section.id}`;
      permalink.title = "Link to this bookable on this date";
      heading.append(permalink);
      section.setAttribute("aria-labelledby", heading.id);
      const header = el("header", undefined, "bookable-area__header");
      header.append(heading, el("p", bookableSubtitle(booking)));
      const tables = el("div", undefined, "bookable-tables");
      section.append(header, tables);
      area = { section, tables };
      areas.set(booking.bookable_id, area);
    }
    area.tables.append(renderCard(booking, context, { grouped: true }));
  }
  return Array.from(areas.values(), (area) => area.section);
}

function browseCard(booking, context, options) {
  const card = bookingCard(booking, context.requestedSeats.get(booking.id) || 0, options);
  const unavailable = requestUnavailable(booking, context);
  if (unavailable) {
    const disabled = el("button", "Request spots", "p-button");
    disabled.type = "button";
    disabled.disabled = true;
    const explanation = el("p", unavailable.reason);
    explanation.id = `request-reason-${booking.id}`;
    disabled.setAttribute("aria-describedby", explanation.id);
    if (unavailable.href) {
      const link = el("a", unavailable.label);
      link.href = unavailable.href;
      explanation.append(" ", link);
    }
    card.append(disabled, explanation);
  } else {
    card.append(requestForm(booking));
  }
  return card;
}

let selectedDate;
let pendingAnchor = true;

function selectDateTab(tab, focus = false) {
  const container = tab.closest(".date-tabs");
  for (const entry of container.querySelectorAll('[role="tab"]')) {
    const active = entry === tab;
    entry.setAttribute("aria-selected", String(active));
    entry.tabIndex = active ? 0 : -1;
  }
  for (const panel of container.querySelectorAll('[role="tabpanel"]')) {
    panel.hidden = panel.id !== tab.getAttribute("aria-controls");
  }
  selectedDate = tab.dataset.date;
  if (focus) tab.focus();
}

function followAnchor() {
  if (!pendingAnchor || !/^#(?:day-\d{4}-\d{2}-\d{2}|booking-\d+|bookable-\d+-\d{4}-\d{2}-\d{2})$/.test(location.hash)) return;
  const target = document.getElementById(location.hash.slice(1));
  if (!target || !content.contains(target)) return;
  const panel = target.closest(".date-tabs__panel");
  if (panel) selectDateTab(document.getElementById(panel.getAttribute("aria-labelledby")));
  for (let parent = target.parentElement; parent && parent !== content; parent = parent.parentElement) {
    if (parent.tagName === "DETAILS") parent.open = true;
  }
  target.scrollIntoView({ block: "start" });
  pendingAnchor = false;
}

window.addEventListener("hashchange", () => {
  pendingAnchor = true;
  followAnchor();
});

function dateTabs(bookings, context, renderDay = bookableAreas, includeEmptyDates = false) {
  const days = new Map();
  if (includeEmptyDates) {
    const today = wallTime(new Date()).split("T")[0];
    const date = new Date(`${today}T12:00:00Z`);
    const minimumEnd = new Date(date);
    minimumEnd.setUTCDate(minimumEnd.getUTCDate() + 7);
    let lastDay = minimumEnd.toISOString().slice(0, 10);
    for (const booking of bookings) {
      const day = wallTime(booking.starts_at).split("T")[0];
      if (day > lastDay) lastDay = day;
    }
    while (date.toISOString().slice(0, 10) <= lastDay) {
      days.set(date.toISOString().slice(0, 10), []);
      date.setUTCDate(date.getUTCDate() + 1);
    }
  }
  for (const booking of bookings) {
    const day = wallTime(booking.starts_at).split("T")[0];
    if (!days.has(day)) days.set(day, []);
    days.get(day).push(booking);
  }
  const linkedBookable = /^#bookable-(\d+)-(\d{4}-\d{2}-\d{2})$/.exec(location.hash);
  const linkedBooking = /^#booking-(\d+)$/.exec(location.hash);
  const booking = linkedBooking && bookings.find((entry) => String(entry.id) === linkedBooking[1]);
  const linkedDay = booking ? wallTime(booking.starts_at).split("T")[0]
    : linkedBookable?.[2] ?? /^#day-(\d{4}-\d{2}-\d{2})$/.exec(location.hash)?.[1];
  if (days.has(linkedDay)) selectedDate = linkedDay;
  if (!days.has(selectedDate)) selectedDate = days.keys().next().value;
  const container = el("div", undefined, "date-tabs");
  const nav = el("nav", undefined, "p-tabs");
  nav.setAttribute("aria-label", "Booking dates");
  const list = el("ul", undefined, "p-tabs__list");
  list.setAttribute("role", "tablist");
  nav.append(list);
  container.append(nav);
  const tabs = [];
  for (const [day, dayBookings] of days) {
    const selected = day === selectedDate;
    const item = el("li", undefined, "p-tabs__item");
    item.setAttribute("role", "presentation");
    const tab = el("a", undefined, "p-tabs__link");
    tab.href = `#day-${day}`;
    tab.title = "Link to this date";
    tab.id = `tab-${day}`;
    tab.dataset.date = day;
    tab.setAttribute("role", "tab");
    tab.setAttribute("aria-controls", `day-${day}`);
    tab.setAttribute("aria-selected", String(selected));
    tab.tabIndex = selected ? 0 : -1;
    tab.append(
      el("span", tabDateFormat.format(new Date(`${day}T12:00:00Z`))),
      el("span", String(dayBookings.length), "date-tabs__count"),
    );
    const hasOwn = Boolean(user.login) && dayBookings.some((booking) => booking.owner_login === user.login);
    const hasRequest = dayBookings.some((booking) => context.requestedSeats.has(booking.id));
    const markers = [];
    if (hasOwn) {
      tab.append(el("span", "", "date-tabs__marker date-tabs__marker--mine"));
      markers.push("your booking");
    }
    if (hasRequest) {
      tab.append(el("span", "", "date-tabs__marker date-tabs__marker--requested"));
      markers.push("your request");
    }
    tab.setAttribute(
      "aria-label",
      `${dateOnlyFormat.format(new Date(`${day}T12:00:00Z`))}, ${bookingCount(dayBookings.length)}`
        + (markers.length ? `, includes ${markers.join(" and ")}` : ""),
    );
    item.append(tab);
    list.append(item);
    const panel = el("div", undefined, "date-tabs__panel");
    panel.id = `day-${day}`;
    panel.setAttribute("role", "tabpanel");
    panel.setAttribute("aria-labelledby", tab.id);
    panel.tabIndex = 0;
    panel.hidden = !selected;
    panel.append(...(dayBookings.length
      ? renderDay(dayBookings, context)
      : [el("p", "No spots booked or requested on this date.")]));
    container.append(panel);
    tabs.push({ day, tab, panel });
  }
  function select(index, focus) {
    selectDateTab(tabs[index].tab, focus);
    if (location.hash !== tabs[index].tab.hash) {
      history.pushState(null, "", tabs[index].tab.hash);
    }
    pendingAnchor = false;
  }
  tabs.forEach(({ tab }, index) => {
    tab.addEventListener("click", (event) => {
      if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      select(index, false);
    });
    tab.addEventListener("keydown", (event) => {
      if (event.key === " ") {
        event.preventDefault();
        select(index, false);
        return;
      }
      const moves = { ArrowRight: index + 1, ArrowLeft: index - 1, Home: 0, End: tabs.length - 1 };
      if (!(event.key in moves)) return;
      event.preventDefault();
      select((moves[event.key] + tabs.length) % tabs.length, true);
    });
  });
  return container;
}

function bookableLabel(bookable) {
  return bookable.area ? `${bookable.name} / ${bookable.area}` : bookable.name;
}

const BOOKABLE_TYPES = [["excursion", "Excursions"], ["restaurant", "Restaurants"], ["sports", "Sports"]];

// Options form a tree: each type ("type:<type>") followed by its indented bookables ("bookable:<id>").
function fillBookables(select, placeholder, list) {
  const selected = select.value;
  const blank = el("option", placeholder);
  blank.value = "";
  const options = [blank];
  for (const [type, label] of BOOKABLE_TYPES) {
    const members = list.filter((bookable) => bookable.type === type)
      .sort((a, b) => bookableLabel(a).localeCompare(bookableLabel(b)));
    if (!members.length) continue;
    const group = el("option", label, "filter-type");
    group.value = `type:${type}`;
    options.push(group, ...members.map((bookable) => {
      const option = el("option", `\u00a0\u00a0\u00a0\u00a0${bookableLabel(bookable)}`);
      option.value = `bookable:${bookable.id}`;
      return option;
    }));
  }
  select.replaceChildren(...options);
  select.value = options.some((option) => option.value === selected) ? selected : "";
}

async function bookableId(form) {
  const type = field(form, "bookable_type").value;
  const candidates = bookables.filter((bookable) => bookable.type === type);
  const name = field(form, "bookable_name").value.trim();
  const area = field(form, "bookable_area").value.trim() || null;
  if (!name) throw new Error("Choose a bookable or enter a new name.");
  const existing = candidates.find((bookable) =>
    bookable.name.toLowerCase() === name.toLowerCase()
      && (area === null || (bookable.area || "").toLowerCase() === area.toLowerCase()));
  if (existing) {
    populateBookable(form, existing);
    return existing.id;
  }
  const bookable = await api("/api/bookables", { method: "POST", body: { type, name, area } });
  if (!bookables.some((item) => item.id === bookable.id)) bookables.push(bookable);
  fillBookables(field(form, "bookable"), "New bookable", bookables);
  populateBookable(form, bookable);
  return bookable.id;
}

function populateBookable(form, bookable) {
  field(form, "bookable").value = `bookable:${bookable.id}`;
  field(form, "bookable_type").value = bookable.type;
  field(form, "bookable_name").value = bookable.name;
  field(form, "bookable_area").value = bookable.area || "";
}

function pad(value) {
  return String(value).padStart(2, "0");
}

function fillOptions(select, values) {
  select.replaceChildren(...values.map((value) => {
    const option = el("option", value);
    option.value = value;
    return option;
  }));
}

function setTimeFields(form, wall) {
  const [day, time] = wall.split("T");
  const [hour, minute] = time.split(":");
  field(form, "date").value = day;
  field(form, "hour").value = hour;
  const minutes = field(form, "minute");
  // Keep a legacy off-boundary minute selectable so an unchanged edit stays valid.
  if (![...minutes.options].some((option) => option.value === minute)) {
    const option = el("option", minute);
    option.value = minute;
    minutes.append(option);
  }
  minutes.value = minute;
}

function bookingForm(booking, onSave) {
  const form = document.querySelector("#booking-form").content.firstElementChild.cloneNode(true);
  fillOptions(field(form, "hour"), Array.from({ length: 24 }, (_, hour) => pad(hour)));
  fillOptions(field(form, "minute"), ["00", "15", "30", "45"]);
  const bookableSelect = field(form, "bookable");
  const typeInput = field(form, "bookable_type");
  const nameInput = field(form, "bookable_name");
  const areaInput = field(form, "bookable_area");
  fillBookables(bookableSelect, "New bookable", bookables);
  typeInput.value = booking?.bookable_type || "restaurant";
  bookableSelect.addEventListener("change", () => {
    const [kind, value] = bookableSelect.value.split(":");
    if (kind === "bookable") {
      populateBookable(form, bookables.find((bookable) => String(bookable.id) === value));
    } else {
      if (kind === "type") typeInput.value = value;
      nameInput.value = "";
      areaInput.value = "";
    }
  });
  const syncBookableSelect = () => {
    const existing = bookables.find((bookable) =>
      bookable.type === typeInput.value
      && bookable.name.toLowerCase() === nameInput.value.trim().toLowerCase()
      && (bookable.area || "").toLowerCase() === areaInput.value.trim().toLowerCase());
    bookableSelect.value = existing ? `bookable:${existing.id}` : "";
  };
  for (const control of [nameInput, areaInput]) {
    control.addEventListener("input", syncBookableSelect);
  }
  typeInput.addEventListener("change", syncBookableSelect);
  setTimeFields(form, `${wallTime(Date.now() + 86400000).split("T")[0]}T19:00`);
  if (booking) {
    field(form, "name").value = booking.name;
    populateBookable(form, {
      id: booking.bookable_id,
      type: booking.bookable_type,
      name: booking.bookable_name,
      area: booking.area,
    });
    setTimeFields(form, wallTime(booking.starts_at));
    field(form, "duration_hours").value = booking.duration_minutes / 60;
    field(form, "party_size").value = booking.party_size;
    field(form, "seats_available").value = booking.seats_available;
    field(form, "description").value = booking.description;
  }
  const requestedSeats = booking?.locked
    ? booking.requests.filter((request) => request.status === "active").reduce((sum, request) => sum + request.seats, 0)
    : 0;
  if (booking?.locked) {
    for (const control of form.querySelectorAll("input, select, textarea")) {
      control.disabled = !["party_size", "seats_available"].includes(control.name);
    }
    const partySize = field(form, "party_size");
    const spareSeats = field(form, "seats_available");
    partySize.min = String(requestedSeats + 1);
    const updateSpareLimit = () => {
      const capacity = partySize.valueAsNumber;
      if (!Number.isInteger(capacity) || capacity < requestedSeats + 1) return;
      const maximum = capacity - requestedSeats - 1;
      spareSeats.max = String(maximum);
      if (spareSeats.valueAsNumber > maximum) spareSeats.value = String(maximum);
    };
    partySize.addEventListener("input", updateSpareLimit);
    updateSpareLimit();
    form.prepend(el("p",
      `Other booking details are locked. Keep at least ${requestedSeats + 1} spots: one for the host plus ${requestedSeats} requested spot(s). Spare spots exclude those requests.`));
  }
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    busy(form.querySelector("button[type=submit]"), async () => {
      const partySize = Number(field(form, "party_size").value);
      const seats = Number(field(form, "seats_available").value);
      if (seats > partySize) throw new Error("Spare spots cannot exceed party size.");
      if (booking?.locked) {
        if (partySize < requestedSeats + 1) {
          throw new Error(`Booking size must be at least ${requestedSeats + 1} to keep one host spot and all active requests.`);
        }
        if (seats > partySize - requestedSeats - 1) {
          throw new Error("Spare spots must leave room for one host spot and all active requests.");
        }
        await onSave({ party_size: partySize, seats_available: seats });
        return;
      }
      const hours = field(form, "duration_hours").value;
      const minutes = hours === "" ? 120 : Number(hours) * 60;
      const duration = Math.round(minutes);
      if (!Number.isSafeInteger(duration) || duration < 1 || Math.abs(minutes - duration) > 1e-7) {
        throw new Error("Duration must be a positive whole number of minutes.");
      }
      const body = {
        name: field(form, "name").value,
        bookable_id: await bookableId(form),
        starts_at: `${field(form, "date").value}T${field(form, "hour").value}:${field(form, "minute").value}`,
        timezone: timeZone,
        duration_minutes: duration,
        party_size: partySize,
        seats_available: seats,
        description: field(form, "description").value,
      };
      // Preserve the exact stored instant and date zone if an edit did not change the time.
      if (booking && body.starts_at === wallTime(booking.starts_at)) {
        body.starts_at = booking.starts_at;
        body.timezone = booking.timezone;
      }
      await onSave(body);
    });
  });
  return form;
}

function requestForm(booking) {
  const details = el("details", undefined, "request-control");
  details.append(el("summary", "Request spots", "p-button"));
  const form = el("form");
  const seatsLabel = el("label", "Spots requested");
  const seats = el("input");
  seats.type = "number";
  seats.min = "1";
  seats.max = String(booking.seats_available);
  seats.value = "1";
  seats.required = true;
  seatsLabel.append(seats);
  const submit = el("button", "Confirm request", "p-button--positive");
  submit.type = "submit";
  form.append(seatsLabel, submit);
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    busy(submit, async () => {
      await api(`/api/bookings/${booking.id}/requests`, {
        method: "POST", body: { seats: Number(seats.value) },
      });
      await render();
      notify("Spots requested. View or cancel them in My spots.");
    });
  });
  details.append(form);
  return details;
}

async function browse() {
  content.append(el("h1", "Find spots for a meal, excursion, or game"));
  const filters = el("form", undefined, "browse-filters");
  const bookableLabel = el("label", "Bookable");
  const bookable = el("select");
  bookable.name = "bookable";
  fillBookables(bookable, "All bookables", []);
  bookableLabel.append(bookable);
  const seatsLabel = el("label", "Minimum spare spots");
  const seats = el("input");
  seats.type = "number";
  seats.name = "min_seats";
  seats.min = "1";
  seats.value = "1";
  seats.required = true;
  seatsLabel.append(seats);
  filters.append(bookableLabel, seatsLabel);
  const results = el("section");
  results.setAttribute("aria-label", "Available bookings");
  results.setAttribute("aria-live", "polite");
  content.append(filters, results);
  let revision = 0;
  let debounce;
  let lastQuery;
  const query = () => `${filters.checkValidity()}|${bookable.value}|${seats.value}`;
  async function load(current) {
    lastQuery = query();
    try {
      if (!filters.checkValidity()) {
        results.replaceChildren(el("p", "Enter valid filter values to see bookings."));
        return;
      }
      const params = new URLSearchParams({ min_seats: seats.value });
      const [kind, value] = bookable.value.split(":");
      if (kind === "type") params.set("bookable_type", value);
      if (kind === "bookable") params.set("bookable_id", value);
      const [bookings, requests, ownedBookings, activeBookables] = await Promise.all([
        api(`/api/bookings?${params}`),
        user.login ? api("/api/requests/mine") : Promise.resolve([]),
        user.login ? api("/api/bookings/mine") : Promise.resolve([]),
        api("/api/bookables?active=true"),
      ]);
      if (current !== revision) return;
      const filtered = bookable.value;
      fillBookables(bookable, "All bookables", activeBookables);
      if (bookable.value !== filtered) {
        // The filtered bookable or type no longer has active bookings; show all instead.
        lastQuery = undefined;
        scheduleLoad();
        return;
      }
      const active = requests.filter((request) => request.status === "active");
      const requestedSeats = new Map();
      for (const request of active) {
        requestedSeats.set(request.booking_id, (requestedSeats.get(request.booking_id) || 0) + request.seats);
      }
      if (current !== revision) return;
      results.replaceChildren(el("p", `${bookingCount(bookings.length)} shown`));
      if (bookings.length) results.append(seatLegend(),
        dateTabs(bookings, { activeRequests: active, requestedSeats, ownedBookings }));
      expireAt(bookings, () => {
        lastQuery = undefined;
        scheduleLoad();
      });
    } catch (error) {
      if (current !== revision) return;
      const notice = el("p", error instanceof Error ? error.message : String(error));
      notice.setAttribute("role", "alert");
      results.replaceChildren(notice);
    } finally {
      if (current === revision) {
        results.setAttribute("aria-busy", "false");
        followAnchor();
      }
    }
  }
  function scheduleLoad() {
    // A blur "change" after typing repeats the last query; reloading would steal focus.
    if (query() === lastQuery) return;
    clearTimeout(debounce);
    const current = ++revision;
    results.setAttribute("aria-busy", "true");
    debounce = setTimeout(() => load(current), 250);
  }
  filters.addEventListener("input", scheduleLoad);
  filters.addEventListener("change", scheduleLoad);
  filters.addEventListener("submit", (event) => event.preventDefault());
  results.setAttribute("aria-busy", "true");
  await load(++revision);
}

function requestTable(requests) {
  const table = el("table");
  const caption = el("caption", "Spot requests (visible only to you)");
  const head = el("thead");
  const row = el("tr");
  for (const heading of ["GitHub", "Spots", "Status"]) {
    const cell = el("th", heading);
    cell.scope = "col";
    row.append(cell);
  }
  head.append(row);
  const body = el("tbody");
  for (const request of requests) {
    const row = el("tr");
    const login = el("td");
    login.append(githubLink(request.requester_login));
    row.append(login);
    for (const value of [request.seats, request.status]) row.append(el("td", value));
    body.append(row);
  }
  table.append(caption, head, body);
  return table;
}

function mySeatsCard(booking, context, options = {}) {
  const mine = booking.owner_login === user.login;
  const requests = context.requestsByBooking.get(booking.id) || [];
  const isExpired = !future(booking);
  const card = bookingCard(booking, context.requestedSeats.get(booking.id) || 0,
    {
      ...options,
      hasRequests: requests.length > 0,
      ownerStatus: mine && !isExpired
        ? booking.locked ? "Details locked; spots editable" : "Editable"
        : undefined,
    });
  if (mine) {
    if (booking.requests.length) card.append(requestTable(booking.requests));
    if (isExpired) return card;
    const actions = el("div", undefined, "card-actions");
    const toggle = button(booking.closed ? "Reopen booking" : "Close booking", async () => {
      await api(`/api/bookings/${booking.id}/${booking.closed ? "reopen" : "close"}`, {
        method: "POST",
      });
      await render();
      notify(booking.closed ? "Booking reopened." : "Booking closed.");
    });
    toggle.disabled = booking.closed && booking.seats_available === 0;
    actions.append(toggle);
    if (!booking.locked) {
      actions.append(button("Delete booking", async () => {
        if (!window.confirm("Delete this booking?")) return;
        await api(`/api/bookings/${booking.id}`, { method: "DELETE" });
        await render();
        notify("Booking deleted.");
      }, "p-button--negative"));
    }
    const edit = el("details");
    edit.append(el("summary", booking.locked ? "Adjust booking size / spare spots" : "Edit booking"),
      bookingForm(booking, async (body) => {
        await api(`/api/bookings/${booking.id}`, { method: "PATCH", body });
        await render();
        notify("Booking updated.");
      }));
    card.append(edit);
    card.append(actions);
    return card;
  }
  for (const request of requests) {
    card.append(el("p", request.status === "cancelled"
      ? `Cancelled request for ${request.seats} spot(s)`
      : `${request.seats} spot(s) occupied by your ${isExpired ? "expired" : "active"} request`));
    if (request.status === "active" && !isExpired) {
      card.append(button("Cancel request", async () => {
        if (!window.confirm("Cancel your spot request?")) return;
        await api(`/api/requests/${request.id}`, { method: "DELETE" });
        await render();
        notify("Request cancelled and spots returned.");
      }));
    }
  }
  return card;
}

function expiredSection(cards) {
  const section = el("details", undefined, "expired-section");
  section.append(el("summary", `Expired (${cards.length})`), ...cards);
  return section;
}

async function mySeats() {
  content.append(el("h1", "My spots"));
  const [owned, requests] = await Promise.all([
    api("/api/bookings/mine"),
    api("/api/requests/mine"),
  ]);
  const tables = new Map(owned.map((booking) => [booking.id, booking]));
  const requestsByBooking = new Map();
  const requestedSeats = new Map();
  for (const request of requests) {
    if (!tables.has(request.booking_id)) {
      tables.set(request.booking_id, { ...request, id: request.booking_id, name: request.booking_name });
    }
    if (!requestsByBooking.has(request.booking_id)) requestsByBooking.set(request.booking_id, []);
    requestsByBooking.get(request.booking_id).push(request);
    if (request.status === "active") {
      requestedSeats.set(request.booking_id, (requestedSeats.get(request.booking_id) || 0) + request.seats);
    }
  }
  const bookings = Array.from(tables.values()).sort((a, b) => new Date(a.starts_at) - new Date(b.starts_at) || a.id - b.id);
  const upcoming = bookings.filter(future);
  const expired = bookings.filter((booking) => !future(booking)).reverse();
  const context = { requestsByBooking, requestedSeats };
  if (!bookings.length) {
    content.append(el("p", "No spots yet. Share a booking with New booking or request spots in Browse."));
  }
  expireAt(upcoming, renderWhenIdle);
  if (bookings.length) content.append(seatLegend());
  content.append(el("h2", "Upcoming", "p-heading--4"),
    dateTabs(upcoming, context, (dayBookings) => bookableAreas(dayBookings, context, mySeatsCard), true));
  if (expired.length) content.append(expiredSection(expired.map((booking) => mySeatsCard(booking, context))));
}

async function render() {
  content.setAttribute("aria-busy", "true");
  clearTimeout(expiryTimer);
  content.replaceChildren();
  try {
    if (location.pathname !== "/" && !user.login) {
      content.append(el("h1", "Sign in to continue"));
      const link = el("a", "Sign in with GitHub", "p-button--positive");
      link.href = "/auth/login";
      content.append(link);
    } else if (location.pathname === "/new") {
      content.append(el("h1", "Share spots on your booking"), bookingForm(null, async (body) => {
        await api("/api/bookings", { method: "POST", body });
        location.assign("/mine");
      }));
    } else if (location.pathname === "/mine") {
      await mySeats();
    } else {
      await browse();
    }
  } finally {
    content.setAttribute("aria-busy", "false");
    followAnchor();
  }
}

async function start() {
  user = await api("/api/me");
  setCsrf(user.csrf_token);
  timeFormat = new Intl.DateTimeFormat(undefined, {
    timeZone, hour: "2-digit", minute: "2-digit", hourCycle: "h23",
  });
  dateFormat = new Intl.DateTimeFormat(undefined, {
    timeZone, dateStyle: "full", timeStyle: "short", hourCycle: "h23",
  });
  dayFormat = new Intl.DateTimeFormat("en-CA", { timeZone, year: "numeric", month: "2-digit", day: "2-digit" });
  document.querySelector("#timezone").textContent = `All times shown in your time zone (${timeZone})`;
  for (const link of document.querySelectorAll("nav a")) {
    if (link.pathname === location.pathname) {
      link.setAttribute("aria-current", "page");
      document.title = `${link.textContent} · Seat Share`;
    }
  }
  if (user.login) {
    document.querySelector("#auth").replaceChildren(
      withLogin("Signed in as ", user.login),
      button("Sign out", async () => {
        await api("/auth/logout", { method: "POST" });
        location.assign("/");
      }),
    );
  }
  bookables = await api("/api/bookables");
  await render();
}

start().catch((error) => {
  content.setAttribute("aria-busy", "false");
  content.replaceChildren(el("p", "Seat Share could not load. Refresh to retry."));
  handleError(error);
});
