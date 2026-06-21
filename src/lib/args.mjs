// Minimal CLI argument parser. No dependencies.
// spec: { name: {aliases:[...], type:'string'|'int'|'float'|'bool'|'list', default} }
export function parseArgs(argv, spec = {}) {
  const aliasMap = new Map();
  for (const [name, def] of Object.entries(spec)) {
    aliasMap.set(`--${name}`, name);
    for (const a of def.aliases || []) aliasMap.set(a, name);
  }

  const opts = {};
  const positionals = [];
  for (const [name, def] of Object.entries(spec)) {
    if (def.type === 'list') opts[name] = [];
    else if ('default' in def) opts[name] = def.default;
  }

  const coerce = (def, raw) => {
    if (def.type === 'int') return parseInt(raw, 10);
    if (def.type === 'float') return parseFloat(raw);
    return raw;
  };

  for (let i = 0; i < argv.length; i++) {
    let tok = argv[i];
    if (tok === '--') {
      positionals.push(...argv.slice(i + 1));
      break;
    }
    if (tok.startsWith('-') && tok !== '-' && !/^-\d/.test(tok)) {
      let inlineVal = null;
      const eq = tok.indexOf('=');
      if (eq !== -1) {
        inlineVal = tok.slice(eq + 1);
        tok = tok.slice(0, eq);
      }
      const name = aliasMap.get(tok);
      if (!name) {
        throw new Error(`Unknown option: ${tok}`);
      }
      const def = spec[name];
      if (def.type === 'bool') {
        opts[name] = true;
        continue;
      }
      const raw = inlineVal !== null ? inlineVal : argv[++i];
      if (raw === undefined) throw new Error(`Option ${tok} requires a value`);
      if (def.type === 'list') opts[name].push(coerce(def, raw));
      else opts[name] = coerce(def, raw);
    } else {
      positionals.push(tok);
    }
  }
  return { opts, positionals };
}
