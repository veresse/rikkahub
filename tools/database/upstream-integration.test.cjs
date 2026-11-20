// Run without Gradle: node --test tools/database/upstream-integration.test.cjs
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { DatabaseSync } = require('node:sqlite');
const { readFileSync } = require('node:fs');
const { execFileSync } = require('node:child_process');
const path = require('node:path');
const root = path.resolve(__dirname, '../..');
const schemas = 'app/schemas/me.rerere.rikkahub.data.db.AppDatabase/';
const migrations = 'app/src/main/java/me/rerere/rikkahub/data/db/migrations/';
const historical = (revision, version) => JSON.parse(execFileSync('git', ['show', `${revision}:${schemas}${version}.json`], { cwd: root, encoding: 'utf8' })).database;
const sqlFor = (version) => {
  const source = readFileSync(path.join(root, migrations, `Migration_${version}.kt`), 'utf8');
  return [...source.matchAll(/db\.execSQL\(\s*("""[\s\S]*?"""\.trimIndent\(\)|"[^"\n]*"(?:\s*\+\s*"[^"\n]*")*)\s*\)/g)]
    .map(([, value]) => value.startsWith('"""') ? value.slice(3, value.lastIndexOf('"""')) : [...value.matchAll(/"([^"\n]*)"/g)].map(([, part]) => part).join(''));
};
const migrate = (db, version) => {
  const source = readFileSync(path.join(root, migrations, `Migration_${version}.kt`), 'utf8');
  if (source.includes('Migration_32_33.migrate(db)')) migrate(db, '32_33');
  for (const sql of sqlFor(version)) db.exec(sql);
};
const create = (db, schema) => {
  for (const entity of schema.entities) {
    db.exec(entity.createSql.replaceAll('${TABLE_NAME}', entity.tableName));
    for (const index of entity.indices ?? []) db.exec(index.createSql.replaceAll('${TABLE_NAME}', entity.tableName));
  }
};
const snapshot = (db) => db.prepare("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name").all().map(({ name }) => ({
  name,
  columns: db.prepare(`PRAGMA table_info(\`${name}\`)`).all(),
  foreignKeys: db.prepare(`PRAGMA foreign_key_list(\`${name}\`)`).all(),
  indices: db.prepare(`PRAGMA index_list(\`${name}\`)`).all().map(({ name: index, unique, origin, partial }) => ({ name: index, unique, origin, partial, columns: db.prepare(`PRAGMA index_info(\`${index}\`)`).all() })).sort((a, b) => a.name.localeCompare(b.name)),
}));
const fork33 = historical('c7be873b', 33);
const upstream34 = historical('358e2931', 34);
const expected = { entities: [...upstream34.entities, ...fork33.entities.filter(e => e.tableName.startsWith('auto_chat_'))] };
test('generated Room schema matches the migration target', () => {
  const actual = new DatabaseSync(':memory:');
  const target = new DatabaseSync(':memory:');
  create(actual, JSON.parse(readFileSync(path.join(root, schemas, '35.json'), 'utf8')).database);
  create(target, expected);
  assert.deepEqual(snapshot(actual), snapshot(target));
  actual.close();
  target.close();
});
test('database version and factory register the explicit fork upgrade paths', () => {
  const database = readFileSync(path.join(root, 'app/src/main/java/me/rerere/rikkahub/data/db/AppDatabase.kt'), 'utf8');
  const factory = readFileSync(path.join(root, 'app/src/main/java/me/rerere/rikkahub/data/db/AppDatabaseFactory.kt'), 'utf8');
  assert.match(database, /version = 35/);
  assert.doesNotMatch(database, /AutoMigration\(from = (32|33),/);
  const registered = factory.slice(factory.indexOf('.addMigrations('), factory.indexOf('.addCallback('));
  for (const name of ['Migration_32_33', 'Migration_33_35', 'Migration_34_35']) assert.ok(registered.includes(name));
  for (const file of [schemas + '33.json', migrations + 'Migration_32_33.kt']) {
    assert.equal(readFileSync(path.join(root, file), 'utf8').replaceAll('\r\n', '\n'), execFileSync('git', ['show', `c7be873b:${file}`], { cwd: root, encoding: 'utf8' }).replaceAll('\r\n', '\n'));
  }
});
const seed = (db, table) => {
  const columns = db.prepare(`PRAGMA table_info(\`${table}\`)`).all();
  const values = columns.map(c => {
    if (c.name === 'conversation_id') return 'saved-ConversationEntity-id';
    if (c.name === 'rule_id') return 'saved-auto_chat_rules-id';
    if (!c.notnull && !c.pk) return null;
    return c.type === 'TEXT' ? `saved-${table}-${c.name}` : 7;
  });
  db.prepare(`INSERT INTO \`${table}\` (${columns.map(c => `\`${c.name}\``).join(',')}) VALUES (${values.map(() => '?').join(',')})`).run(...values);
  return db.prepare(`SELECT * FROM \`${table}\``).get();
};
for (const start of [32, 33, 'upstream33', 34]) {
  test(`upgrade ${start} to 35 preserves records and matches merged entities`, () => {
    const db = new DatabaseSync(':memory:');
    create(db, start === 34 ? upstream34 : start === 'upstream33' ? historical('358e2931', 33) : historical('c7be873b', start));
    const saved = Object.fromEntries(['ConversationEntity', 'message_node', 'workspaces', ...(start === 33 ? ['auto_chat_rules', 'auto_chat_records'] : [])].map(table => [table, seed(db, table)]));
    if (start === 32) migrate(db, '32_33');
    migrate(db, `${start === 34 ? 34 : 33}_35`);
    const fresh = new DatabaseSync(':memory:');
    create(fresh, expected);
    assert.deepEqual(snapshot(db), snapshot(fresh));
    for (const [table, row] of Object.entries(saved)) {
      assert.deepEqual(db.prepare(`SELECT ${Object.keys(row).map(c => `\`${c}\``).join(',')} FROM \`${table}\``).get(), row);
    }
    assert.equal(db.prepare('SELECT storage_mounts FROM workspaces').get().storage_mounts, start === 34 ? saved.workspaces.storage_mounts : '[]');
    assert.deepEqual(db.prepare('PRAGMA foreign_key_check').all(), []);
    db.close();
    fresh.close();
  });
}
