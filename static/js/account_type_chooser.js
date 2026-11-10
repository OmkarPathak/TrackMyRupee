/* "What kind of account is it?" on the Add account page.

   A new person either types what they have ("ppf", "credit card", "gold") or picks one of six plain kinds and then
   the exact type inside it, instead of scanning a dropdown of 34.
   This only enhances the page: the type dropdown (#id_account_type) stays in the form as the one source of truth, and
   everything that reads it (the step 2 fields, the server) sees the choice made here. Without this script the page
   shows that dropdown as it always did.

   Stages:  kinds  ->  types (the types inside one kind)  ->  chosen (the usual fields appear).  "Change" goes back.
   Typing in the search box shows matching types from every kind, whatever stage we are in; clearing it brings the
   stage back. */
(function () {
  'use strict';

  var box = document.getElementById('typeChooser');
  var dataEl = document.getElementById('type-categories');
  var select = document.getElementById('id_account_type');
  var fields = document.getElementById('accountBasicsFields');
  if (!box || !dataEl || !select || !fields) return;           // nothing to enhance: the dropdown stays in charge

  var categories;
  try { categories = JSON.parse(dataEl.textContent); } catch (e) { return; }

  var intro = document.getElementById('typeIntro');
  var searchWrap = document.getElementById('typeSearchWrap');
  var search = document.getElementById('typeSearch');
  var kinds = document.getElementById('typeKinds');
  var choices = document.getElementById('typeChoices');
  var results = document.getElementById('typeResults');
  var resultsList = document.getElementById('typeResultsList');
  var noMatch = document.getElementById('typeNoMatch');
  var chosen = document.getElementById('typeChosen');
  var list = document.getElementById('typeChoicesList');
  var title = document.getElementById('typeChoicesTitle');
  var chosenLabel = document.getElementById('typeChosenLabel');
  var nextBtn = document.getElementById('nextBtn');
  var typeColumn = select.closest('[class*="col-"]');
  var which = box.getAttribute('data-label-which') || '';
  var stageName = 'kinds';

  function show(el, on) { if (el) el.classList.toggle('d-none', !on); }

  function optionLabel(code) {
    var option = select.querySelector('option[value="' + code + '"]');
    return option ? option.textContent.trim() : code;
  }

  // Every type with its kind, for searching and for the results list
  var everyType = [];
  categories.forEach(function (category) {
    category.codes.forEach(function (code) {
      everyType.push({ code: code, label: optionLabel(code), kind: category.title });
    });
  });

  function normalise(text) {
    return String(text).toLowerCase().replace(/[^a-z0-9ऀ-ॿ]+/g, ' ').replace(/\s+/g, ' ').trim();
  }

  // Types whose name, code or kind contain every word typed; names that start with the first word come first
  function find(query) {
    var words = normalise(query).split(' ').filter(Boolean);
    if (!words.length) return [];
    var hits = everyType.filter(function (type) {
      var haystack = normalise(type.label + ' ' + type.code.replace(/_/g, ' ') + ' ' + type.kind);
      return words.every(function (word) { return haystack.indexOf(word) !== -1; });
    });
    return hits
      .map(function (type, index) { return { type: type, index: index, rank: normalise(type.label).indexOf(words[0]) === 0 ? 0 : 1 }; })
      .sort(function (a, b) { return a.rank - b.rank || a.index - b.index; })
      .map(function (hit) { return hit.type; });
  }

  // One button per type, in the grid (two across on anything wider than a phone, every row equally tall)
  function fill(container, items, withKind) {
    container.innerHTML = '';
    items.forEach(function (type) {
      var button = document.createElement('button');
      button.type = 'button';
      button.className = 'type-option';
      var name = document.createElement('span');
      name.textContent = type.label;
      button.appendChild(name);
      if (withKind) {
        var kind = document.createElement('span');
        kind.className = 'type-option-kind';
        kind.textContent = type.kind;
        button.appendChild(kind);
      }
      button.addEventListener('click', function () { choose(type.code); });
      container.appendChild(button);
    });
  }

  function refresh() {
    var picking = stageName !== 'chosen';
    var query = picking ? search.value.trim() : '';
    var searching = query !== '';
    show(intro, picking);
    show(searchWrap, picking);
    show(kinds, stageName === 'kinds' && !searching);
    show(choices, stageName === 'types' && !searching);
    show(results, searching);
    show(chosen, stageName === 'chosen');
    show(fields, stageName === 'chosen');
    show(typeColumn, false);                                    // the chosen type is shown above instead
    if (nextBtn) nextBtn.disabled = stageName !== 'chosen';     // nothing to continue with until a type is picked
    if (searching) {
      var hits = find(query);
      fill(resultsList, hits, true);
      noMatch.textContent = search.getAttribute('data-label-none') || '';
      show(noMatch, hits.length === 0);
    }
  }

  function stage(name) { stageName = name; refresh(); }

  function showTypes(category) {
    var items = category.codes.map(function (code) { return { code: code, label: optionLabel(code), kind: category.title }; });
    fill(list, items, false);
    title.textContent = which ? which + ' · ' + category.title : category.title;
    stage('types');
    title.focus();
  }

  function choose(code) {
    select.value = code;
    // The step 2 fields and the styled dropdown both listen for this
    select.dispatchEvent(new Event('change', { bubbles: true }));
    chosenLabel.textContent = optionLabel(code);
    search.value = '';
    stage('chosen');
    var name = document.getElementById('id_name');
    if (name) name.focus();
  }

  function backToKinds() {
    search.value = '';
    stage('kinds');
    search.focus();
  }

  kinds.querySelectorAll('.type-card').forEach(function (card) {
    card.addEventListener('click', function () {
      var key = card.getAttribute('data-category');
      var category = categories.filter(function (c) { return c.key === key; })[0];
      if (category) showTypes(category);
    });
  });
  document.getElementById('typeChoicesBack').addEventListener('click', backToKinds);
  document.getElementById('typeChosenChange').addEventListener('click', backToKinds);

  search.addEventListener('input', refresh);
  search.addEventListener('keydown', function (event) {
    if (event.key === 'Escape') {
      search.value = '';
      refresh();
    } else if (event.key === 'Enter') {
      event.preventDefault();                                   // this box is inside the form: Enter must not submit it
      var hits = find(search.value);
      if (hits.length === 1) {
        choose(hits[0].code);
      } else if (hits.length) {
        var first = resultsList.querySelector('button');
        if (first) first.focus();
      }
    }
  });

  show(box, true);
  stage('kinds');
  // A phone would raise its keyboard over the choices, so only a computer starts in the search box
  if (!(window.matchMedia && window.matchMedia('(pointer: coarse)').matches)) search.focus();
})();
