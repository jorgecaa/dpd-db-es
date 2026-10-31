//// grammatical analyses of one inflected form
//// data comes from grammar_by_word_json.js, rendered in main.js

function grammarByWordRows(form) {
  //// grammar_by_word_json holds, per form, "wordIndex,analysisIndex" pairs into
  //// the two shared tables, so only the form about to be shown is decoded
  const packed = grammar_by_word_json[form];
  if (!packed) {
    return [];
  }

  return packed.split(" ").map((pair) => {
    const indices = pair.split(",");
    const [pos, grammar] = grammar_by_word_analyses[indices[1]];
    return [grammar_by_word_words[indices[0]], pos, grammar];
  });
}

function makeGrammarByWordHtml(form, rows) {
  //// header

  var html = `
        <p class="heading underlined">
            <b>${rows.length}</b>
            grammatical ${rows.length > 1 ? "analyses" : "analysis"} of
            <b>${form}</b>
        </p>
    `;

  //// table
  //// same five columns as the webapp: pos, the three grammar components,
  //// "of", and the headword the analysis belongs to

  html += `
        <table class="grammar_dict">
        <thead>
            <tr>
                <th>pos</th>
                <th></th>
                <th></th>
                <th></th>
                <th></th>
                <th>word</th>
            </tr>
        </thead>
        <tbody>
    `;

  rows.forEach((row) => {
    const word = row[0];
    const pos = row[1];
    const components = splitGrammar(row[2]);

    html += `
            <tr>
                <td><b>${pos}</b></td>
    `;

    components.forEach((component) => {
      html +=
        component === ""
          ? `<td class="col_empty"></td>`
          : `<td>${component}</td>`;
    });

    html += `
                <td>of</td>
                <td>${word}</td>
            </tr>
    `;
  });

  html += `
        </tbody>
        </table>
    `;

  //// footer

  html += `
        <p class="dpd-footer">
        For more information, please
        <a class="dpd-link"
        href="https://digitalpalidictionary.github.io/features/grammardict/"
        target="_blank">
        read the docs
        </a>.
        </p>
    `;

  return html;
}

//// split a grammar string exactly as exporter/webapp/data_classes.py does:
//// "reflx …" and "in comps …" are kept whole, the rest splits on whitespace,
//// then the empty cells that pad the table to three components are added

function splitGrammar(grammar) {
  var components;

  if (grammar.indexOf("reflx") === 0) {
    const parts = grammar.split(" ");
    components =
      parts.length >= 2
        ? [parts[0] + " " + parts[1]].concat(parts.slice(2))
        : [grammar];
  } else if (grammar.indexOf("in comps") === 0) {
    components = [grammar];
  } else {
    components = grammar.split(" ");
  }

  while (components.length < 3) {
    components.push("");
  }

  return components;
}
