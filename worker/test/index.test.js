import assert from "node:assert/strict";
import test from "node:test";

import worker, { calculateDemandScore } from "../src/index.js";

const character = {
  character_number: "001",
  character_id: "c002",
  character_name: "ゴブリン",
};

function createEnvironment({
  snapshot = null,
  mutations = [],
  previousSnapshot = null,
  previousMutations = [],
} = {}) {
  return {
    DB: {
      prepare(sql) {
        return {
          async all() {
            if (sql.includes("FROM market_character_numbers")) {
              return {
                results: [
                  character,
                  {
                    character_number: "002",
                    character_id: "l001",
                    character_name: "フェニックス",
                  },
                ],
              };
            }
            throw new Error(`Unexpected all query: ${sql}`);
          },
          bind(...values) {
            return {
              async first() {
                if (sql.includes("FROM market_character_numbers")) {
                  return values[0] === character.character_number ? character : null;
                }
                if (sql.includes("AND id < ?")) return previousSnapshot;
                if (sql.includes("FROM market_snapshots") && sql.includes("character_id = ?")) {
                  return snapshot;
                }
                if (sql.includes("FROM market_snapshots") && sql.includes("id = ?")) {
                  return snapshot;
                }
                throw new Error(`Unexpected first query: ${sql}`);
              },
              async all() {
                if (sql.includes("FROM market_snapshot_mutations")) {
                  return {
                    results: values[0] === previousSnapshot?.id
                      ? previousMutations
                      : mutations,
                  };
                }
                throw new Error(`Unexpected all query: ${sql}`);
              },
            };
          },
        };
      },
    },
  };
}

function createSubmissionEnvironment({ registeredCharacter = character } = {}) {
  let existingSubmission = null;
  const batches = [];
  const db = {
    prepare(sql) {
      return {
        bind(...values) {
          return {
            sql,
            values,
            async first() {
              if (sql.includes("WHERE submission_id = ?")) return existingSubmission;
              if (sql.includes("FROM market_character_numbers")) {
                return values[0] === registeredCharacter?.character_number
                  ? registeredCharacter
                  : null;
              }
              throw new Error(`Unexpected first query: ${sql}`);
            },
          };
        },
      };
    },
    async batch(statements) {
      batches.push(statements);
      existingSubmission = {
        id: 17,
        status: "pending",
        submitted_at: "2026-10-10T00:00:00Z",
      };
      return [{ meta: { last_row_id: 17 } }, ...statements.slice(1).map(() => ({}))];
    },
  };
  return { DB: db, batches };
}

function createExchangeEnvironment({ registeredCharacter = character } = {}) {
  let existingSubmission = null;
  const batches = [];
  const db = {
    prepare(sql) {
      return {
        bind(...values) {
          return {
            sql,
            values,
            async first() {
              if (sql.includes("FROM exchange_submissions WHERE submission_id = ?")) {
                return existingSubmission;
              }
              if (sql.includes("FROM market_character_numbers")) {
                return values[0] === registeredCharacter?.character_number
                  ? registeredCharacter
                  : null;
              }
              throw new Error(`Unexpected first query: ${sql}`);
            },
          };
        },
      };
    },
    async batch(statements) {
      batches.push(statements);
      existingSubmission = {
        id: 23,
        status: "pending",
        submitted_at: "2026-10-10T00:00:00Z",
      };
      return [{ meta: { last_row_id: 23 } }, ...statements.slice(1).map(() => ({}))];
    },
  };
  return { DB: db, batches };
}

function createTradeReviewListEnvironment(count, itemCount = 3) {
  const trades = Array.from({ length: count }, (_, index) => ({
    id: index + 1,
    trade_type: "bundle",
    total_amount: 900,
    status: "pending",
    submitted_at: "2026-10-10T00:00:00Z",
  }));
  const items = trades.flatMap((trade) =>
    Array.from({ length: itemCount }, (_, index) => ({
      trade_id: trade.id,
      position: index + 1,
      character_id: "c002",
      rarity: "common",
      level: 10,
      mutation: "normal",
      quantity: 1,
      is_verified: 0,
      character_number: "001",
      character_name: "ゴブリン",
    }))
  );
  return {
    DB: {
      prepare(sql) {
        if (sql.includes("COALESCE(MAX(id), 0) AS max_id FROM trades")) {
          return {
            async first() {
              return { max_id: trades.at(-1)?.id || 0 };
            },
          };
        }
        return {
          bind(...values) {
            return {
              async all() {
                if (sql.includes("FROM trades") && sql.includes("ORDER BY id ASC")) {
                  const [after, through, limit] = values;
                  return {
                    results: trades
                      .filter((trade) => trade.id > after && trade.id <= through)
                      .slice(0, limit),
                  };
                }
                if (sql.includes("FROM trade_characters tc")) {
                  return {
                    results: items.filter((item) => values.includes(item.trade_id)),
                  };
                }
                throw new Error(`Unexpected query: ${sql}`);
              },
            };
          },
        };
      },
    },
    addTrade(id) {
      trades.push({
        id,
        trade_type: "single",
        total_amount: 900,
        status: "pending",
        submitted_at: "2026-10-10T00:00:00Z",
      });
      items.push({
        trade_id: id,
        position: 1,
        character_id: "c002",
        rarity: "common",
        level: 10,
        mutation: "normal",
        quantity: 1,
        is_verified: 0,
        character_number: "001",
        character_name: "ゴブリン",
      });
    },
  };
}

function createExchangeReviewListEnvironment(count, itemCount = 3) {
  const exchanges = Array.from({ length: count }, (_, index) => ({
    id: index + 1,
    exchange_description: `交換内容 ${index + 1}`,
    status: "pending",
    submitted_at: "2026-10-10T00:00:00Z",
  }));
  const items = exchanges.flatMap((exchange) =>
    Array.from({ length: itemCount }, (_, index) => ({
      submission_id: exchange.id,
      position: index + 1,
      character_id: "c002",
      rarity: "common",
      level: 10,
      mutation: "normal",
      quantity: 1,
      character_number: "001",
      character_name: "ゴブリン",
    }))
  );
  return {
    DB: {
      prepare(sql) {
        if (sql.includes("COALESCE(MAX(id), 0) AS max_id FROM exchange_submissions")) {
          return {
            async first() {
              return { max_id: exchanges.at(-1)?.id || 0 };
            },
          };
        }
        return {
          bind(...values) {
            return {
              async all() {
                if (
                  sql.includes("FROM exchange_submissions") &&
                  sql.includes("ORDER BY id ASC")
                ) {
                  const [after, through, limit] = values;
                  return {
                    results: exchanges
                      .filter((exchange) => exchange.id > after && exchange.id <= through)
                      .slice(0, limit),
                  };
                }
                if (sql.includes("FROM exchange_submission_characters ec")) {
                  return {
                    results: items.filter((item) =>
                      values.includes(item.submission_id)
                    ),
                  };
                }
                throw new Error(`Unexpected query: ${sql}`);
              },
            };
          },
        };
      },
    },
    addExchange(id) {
      exchanges.push({
        id,
        exchange_description: `交換内容 ${id}`,
        status: "pending",
        submitted_at: "2026-10-10T00:00:00Z",
      });
      items.push({
        submission_id: id,
        position: 1,
        character_id: "c002",
        rarity: "common",
        level: 10,
        mutation: "normal",
        quantity: 1,
        character_number: "001",
        character_name: "ゴブリン",
      });
    },
  };
}

const exchangePayload = {
  submissionId: "76e7b900-2882-46b5-868d-1cc3648c6e8a",
  exchangeDescription: "ゴブリンとフェニックスを交換",
  items: [
    {
      characterNumber: "001",
      rarity: "common",
      level: 10,
      mutation: "normal",
      quantity: 1,
    },
  ],
};

test("lists only characters assigned numbers in D1", async () => {
  const response = await worker.fetch(
    new Request("https://worker.example/characters"),
    createEnvironment(),
  );

  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), {
    ok: true,
    characters: [
      {
        characterNumber: "001",
        characterId: "c002",
        characterName: "ゴブリン",
        rarity: "common",
      },
      {
        characterNumber: "002",
        characterId: "l001",
        characterName: "フェニックス",
        rarity: "legend",
      },
    ],
  });
});

test("allows CORS only for configured browser origins", async () => {
  const allowed = await worker.fetch(
    new Request("https://worker.example/health", {
      headers: { Origin: "https://hakuhatu-843.github.io" },
    }),
    {},
  );
  assert.equal(allowed.headers.get("Access-Control-Allow-Origin"), "https://hakuhatu-843.github.io");
  assert.equal(allowed.headers.get("Vary"), "Origin");

  const untrusted = await worker.fetch(
    new Request("https://worker.example/health", {
      headers: { Origin: "https://attacker.example" },
    }),
    {},
  );
  assert.notEqual(
    untrusted.headers.get("Access-Control-Allow-Origin"),
    "https://attacker.example",
  );
});

test("resolves a public character number to its existing ID and official name", async () => {
  const response = await worker.fetch(
    new Request("https://worker.example/characters/resolve?number=001"),
    createEnvironment(),
  );

  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), {
    ok: true,
    characterNumber: "001",
    characterId: "c002",
    characterName: "ゴブリン",
  });
});

test("returns a clear not-found response for an unregistered number", async () => {
  const response = await worker.fetch(
    new Request("https://worker.example/characters/resolve?number=999"),
    createEnvironment(),
  );

  assert.equal(response.status, 404);
  assert.deepEqual(await response.json(), {
    ok: false,
    error: "Character number not found",
  });
});

test("rejects numbers that are not exactly three digits", async () => {
  const response = await worker.fetch(
    new Request("https://worker.example/characters/resolve?number=1"),
    createEnvironment(),
  );

  assert.equal(response.status, 400);
  assert.deepEqual(await response.json(), {
    ok: false,
    error: "Invalid character number",
  });
});

test("fetches a market snapshot by number without changing character IDs", async () => {
  const snapshot = {
    id: 7,
    character_id: "c002",
    level1_value: 1200,
    level_max_value: 2000,
    demand_score: 2.5,
    sample_count: 4,
    created_at: "2026-10-05T00:00:00Z",
    level1_sample_count: 3,
    level_max_sample_count: 2,
  };
  const response = await worker.fetch(
    new Request("https://worker.example/market-snapshot?characterNumber=001"),
    createEnvironment({
      snapshot,
      mutations: [
        { mutation: "normal", multiplier: 1, sample_count: 4 },
      ],
    }),
  );

  assert.equal(response.status, 200);
  const body = await response.json();
  assert.equal(body.characterNumber, "001");
  assert.equal(body.characterId, "c002");
  assert.equal(body.characterName, "ゴブリン");
  assert.equal(body.level1Value, 1200);
  assert.equal(body.levelMaxValue, 2000);
  assert.equal(body.demandScore, 2.5);
  assert.equal(body.level1SampleCount, 3);
  assert.equal(body.levelMaxSampleCount, 2);
  assert.equal(body.changes.level1Direction, "→");
  assert.equal(body.mutations[0].multiplier, 1);
});

test("reports price, demand, and mutation directions against the prior snapshot", async () => {
  const current = {
    id: 9,
    character_id: "c002",
    level1_value: 1500,
    level_max_value: 1900,
    demand_score: 3.1,
    sample_count: 4,
    created_at: "2026-10-10T00:00:00Z",
    level1_sample_count: 3,
    level_max_sample_count: 3,
  };
  const previous = {
    id: 7,
    character_id: "c002",
    level1_value: 1000,
    level_max_value: 2000,
    demand_score: 3.2,
  };
  const response = await worker.fetch(
    new Request("https://worker.example/market-snapshot?characterNumber=001"),
    createEnvironment({
      snapshot: current,
      previousSnapshot: previous,
      mutations: [
        { mutation: "gold", multiplier: 1.5, sample_count: 3 },
        { mutation: "normal", multiplier: 1, sample_count: 4 },
      ],
      previousMutations: [
        { mutation: "gold", multiplier: 1.7, sample_count: 3 },
        { mutation: "normal", multiplier: 1, sample_count: 4 },
      ],
    }),
  );

  assert.equal(response.status, 200);
  const body = await response.json();
  assert.equal(body.changes.level1Direction, "↑");
  assert.equal(body.changes.levelMaxDirection, "↓");
  assert.equal(body.changes.demandDirection, "↓");
  assert.equal(body.mutations[0].direction, "↓");
  assert.equal(body.mutations[1].direction, "→");
  assert.equal(body.changes.hasPrevious, true);
});

test("computes a median only from approved verified single-trade samples", async () => {
  let selectedSql = "";
  const env = {
    DB: {
      prepare(sql) {
        selectedSql = sql;
        return {
          bind(...values) {
            assert.deepEqual(values, ["c002", "normal", 1, 21]);
            return {
              async all() {
                return {
                  results: [
                    { total_amount: 100, quantity: 1 },
                    { total_amount: 400, quantity: 2 },
                    { total_amount: 900, quantity: 3 },
                  ],
                };
              },
            };
          },
        };
      },
    },
  };
  const response = await worker.fetch(
    new Request(
      "https://worker.example/market-value?characterId=c002&mutation=normal&level=1",
    ),
    env,
  );

  assert.equal(response.status, 200);
  const body = await response.json();
  assert.equal(body.marketValue, 200);
  assert.equal(body.sampleCount, 3);
  assert.match(selectedSql, /t\.status = 'approved'/);
  assert.match(selectedSql, /t\.trade_type = 'single'/);
  assert.match(selectedSql, /tc\.is_verified = 1/);
});

test("does not infer a market price when fewer than three samples exist", async () => {
  const response = await worker.fetch(
    new Request(
      "https://worker.example/market-value?characterId=c002&mutation=normal&level=301",
    ),
    {
      DB: {
        prepare() {
          return {
            bind() {
              return {
                async all() {
                  return { results: [{ total_amount: 900, quantity: 1 }] };
                },
              };
            },
          };
        },
      },
    },
  );
  const body = await response.json();

  assert.equal(response.status, 200);
  assert.equal(body.status, "insufficient_data");
  assert.equal(body.marketValue, null);
  assert.equal(body.sampleCount, 1);
});

test("rejects invalid submission item identity before database writes", async () => {
  const response = await worker.fetch(
    new Request("https://worker.example/trades", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        tradeType: "single",
        submissionId: "76e7b900-2882-46b5-868d-1cc3648c6e8a",
        items: [
          {
            characterNumber: "1",
            rarity: "common",
            level: 10,
            mutation: "normal",
            quantity: 1,
          },
        ],
        totalPrice: 100,
      }),
    }),
    createEnvironment(),
  );

  assert.equal(response.status, 400);
  assert.deepEqual(await response.json(), {
    ok: false,
    error: "Invalid trade item",
  });
});

test("rejects malformed JSON for priced and exchange submissions", async () => {
  for (const path of ["/trades", "/exchanges"]) {
    const response = await worker.fetch(
      new Request(`https://worker.example${path}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{",
      }),
      {},
    );
    assert.equal(response.status, 400);
    assert.deepEqual(await response.json(), { ok: false, error: "Invalid JSON" });
  }
});

test("limits streamed submission bodies before parsing or database access", async () => {
  for (const path of ["/trades", "/exchanges"]) {
    const body = new ReadableStream({
      start(controller) {
        controller.enqueue(new Uint8Array(16 * 1024 + 1));
        controller.close();
      },
    });
    const response = await worker.fetch(
      new Request(`https://worker.example${path}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body,
        duplex: "half",
      }),
      {},
    );
    assert.equal(response.status, 413);
    assert.deepEqual(await response.json(), {
      ok: false,
      error: "Request body too large",
    });
  }
});

test("enforces the shared IP submission limit for priced and exchange entries", async () => {
  for (const [path, payload] of [
    [
      "/trades",
      {
        submissionId: "76e7b900-2882-46b5-868d-1cc3648c6e8a",
        tradeType: "single",
        items: [{
          characterId: "c002",
          rarity: "common",
          level: 10,
          mutation: "normal",
          quantity: 1,
        }],
        totalPrice: 1200,
      },
    ],
    ["/exchanges", exchangePayload],
  ]) {
    const statements = [];
    const env = {
      DB: {
        prepare(sql) {
          return {
            bind(...values) {
              statements.push({ sql, values });
              return {
                async first() {
                  if (sql.includes("FROM blocked_ips")) return null;
                  if (sql.includes("WHERE submission_id = ?")) return null;
                  if (sql.includes("SELECT id, status, submitted_at FROM exchange_submissions")) {
                    return null;
                  }
                  if (sql.includes("AS count")) return { count: 10 };
                  throw new Error(`Unexpected first query: ${sql}`);
                },
              };
            },
          };
        },
        async batch() {
          assert.fail("Rate-limited submissions must not be written");
        },
      },
    };
    const response = await worker.fetch(
      new Request(`https://worker.example${path}`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "CF-Connecting-IP": "192.0.2.4",
        },
        body: JSON.stringify(payload),
      }),
      env,
    );

    assert.equal(response.status, 429);
    assert.match((await response.json()).error, /Too many submissions/);
    assert.ok(statements.some(({ sql }) => sql.includes("FROM trades")));
    assert.ok(statements.some(({ sql }) => sql.includes("FROM exchange_submissions")));
  }
});

test("resolves numbered Web submissions and makes retries idempotent", async () => {
  const env = createSubmissionEnvironment();
  const body = {
    submissionId: "76e7b900-2882-46b5-868d-1cc3648c6e8a",
    tradeType: "single",
    items: [
      {
        characterNumber: "001",
        rarity: "common",
        level: 10,
        mutation: "normal",
        quantity: 1,
      },
    ],
    totalPrice: 1200,
  };
  const post = () =>
    worker.fetch(
      new Request("https://worker.example/trades", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      }),
      env,
    );

  const first = await post();
  const firstBody = await first.json();
  const retry = await post();
  const retryBody = await retry.json();

  assert.equal(first.status, 201);
  assert.equal(firstBody.tradeId, 17);
  assert.equal(firstBody.status, "pending");
  assert.equal(retry.status, 200);
  assert.equal(retryBody.duplicate, true);
  assert.equal(retryBody.tradeId, firstBody.tradeId);
  assert.equal(env.batches.length, 1);
});

test("rejects an unregistered number in a Web submission without inserting it", async () => {
  const env = createSubmissionEnvironment({ registeredCharacter: null });
  const response = await worker.fetch(
    new Request("https://worker.example/trades", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        submissionId: "76e7b900-2882-46b5-868d-1cc3648c6e8a",
        tradeType: "single",
        items: [
          {
            characterNumber: "999",
            rarity: "common",
            level: 10,
            mutation: "normal",
            quantity: 1,
          },
        ],
        totalPrice: 1200,
      }),
    }),
    env,
  );

  assert.equal(response.status, 404);
  assert.equal(env.batches.length, 0);
});

test("stores exchange-only submissions separately without any price field", async () => {
  const env = createExchangeEnvironment();
  const response = await worker.fetch(
    new Request("https://worker.example/exchanges", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(exchangePayload),
    }),
    env,
  );

  assert.equal(response.status, 201);
  const body = await response.json();
  assert.equal(body.exchangeId, 23);
  assert.equal(body.status, "pending");
  assert.equal("totalPrice" in body, false);
  assert.equal(env.batches.length, 1);
  assert.match(env.batches[0][0].sql, /INSERT INTO exchange_submissions/);
  assert.match(env.batches[0][1].sql, /exchange_submission_characters/);
  assert.deepEqual(env.batches[0][0].values.slice(0, 2), [
    exchangePayload.submissionId,
    exchangePayload.exchangeDescription,
  ]);
});

test("returns the prior exchange submission when an identical UUID is retried", async () => {
  const env = createExchangeEnvironment();
  const post = () =>
    worker.fetch(
      new Request("https://worker.example/exchanges", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(exchangePayload),
      }),
      env,
    );

  const first = await post();
  const retry = await post();

  assert.equal(first.status, 201);
  assert.equal(retry.status, 200);
  assert.equal((await retry.json()).duplicate, true);
  assert.equal(env.batches.length, 1);
});

test("rejects empty exchange descriptions and unregistered characters", async () => {
  const emptyDescription = await worker.fetch(
    new Request("https://worker.example/exchanges", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...exchangePayload, exchangeDescription: " " }),
    }),
    createExchangeEnvironment(),
  );
  assert.equal(emptyDescription.status, 400);
  assert.equal((await emptyDescription.json()).error, "Invalid exchange description");

  const fabricatedPrice = await worker.fetch(
    new Request("https://worker.example/exchanges", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...exchangePayload, totalPrice: 100 }),
    }),
    createExchangeEnvironment(),
  );
  assert.equal(fabricatedPrice.status, 400);

  const unknownNumber = await worker.fetch(
    new Request("https://worker.example/exchanges", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        ...exchangePayload,
        items: [{ ...exchangePayload.items[0], characterNumber: "999" }],
      }),
    }),
    createExchangeEnvironment({ registeredCharacter: null }),
  );
  assert.equal(unknownNumber.status, 404);
});

test("requires admin authentication to read exchange review items", async () => {
  const response = await worker.fetch(
    new Request("https://worker.example/exchanges?status=pending"),
    { ...createEnvironment(), ADMIN_KEY: "secret" },
  );

  assert.equal(response.status, 401);
  assert.deepEqual(await response.json(), { ok: false, error: "Unauthorized" });
});

test("returns pending exchange details only to authenticated reviewers", async () => {
  const env = {
    ...createExchangeReviewListEnvironment(1, 1),
    ADMIN_KEY: "secret",
  };
  const response = await worker.fetch(
    new Request("https://worker.example/exchanges?status=pending&limit=50", {
      headers: { "X-Admin-Key": "secret" },
    }),
    env,
  );

  assert.equal(response.status, 200);
  const body = await response.json();
  assert.equal(body.exchanges[0].exchangeDescription, "交換内容 1");
  assert.equal(body.exchanges[0].items[0].characterNumber, "001");
  assert.equal("totalPrice" in body.exchanges[0], false);
  assert.equal(body.hasMore, false);
});

test("pages pending trades oldest-first without truncating multi-item trades", async () => {
  const env = { ...createTradeReviewListEnvironment(51, 3), ADMIN_KEY: "secret" };
  const firstResponse = await worker.fetch(
    new Request("https://worker.example/trades?status=pending&limit=50&after=0", {
      headers: { "X-Admin-Key": "secret" },
    }),
    env,
  );
  const firstPage = await firstResponse.json();

  assert.equal(firstResponse.status, 200);
  assert.equal(firstPage.trades.length, 50);
  assert.equal(firstPage.trades[0].id, 1);
  assert.equal(firstPage.trades[49].id, 50);
  assert.ok(firstPage.trades.every((trade) => trade.items.length === 3));
  assert.equal(firstPage.nextCursor, 50);
  assert.equal(firstPage.throughCursor, 51);
  assert.equal(firstPage.hasMore, true);

  env.addTrade(52);
  const secondResponse = await worker.fetch(
    new Request("https://worker.example/trades?status=pending&limit=50&after=50&through=51", {
      headers: { "X-Admin-Key": "secret" },
    }),
    env,
  );
  const secondPage = await secondResponse.json();
  assert.equal(secondPage.trades.length, 1);
  assert.equal(secondPage.trades[0].id, 51);
  assert.equal(secondPage.trades[0].items.length, 3);
  assert.equal(secondPage.nextCursor, null);
  assert.equal(secondPage.throughCursor, 51);
  assert.equal(secondPage.hasMore, false);
});

test("pages continuing pending exchanges oldest-first with all item rows", async () => {
  const env = {
    ...createExchangeReviewListEnvironment(51, 3),
    ADMIN_KEY: "secret",
  };
  const firstResponse = await worker.fetch(
    new Request("https://worker.example/exchanges?status=pending&limit=50&after=0", {
      headers: { "X-Admin-Key": "secret" },
    }),
    env,
  );
  const firstPage = await firstResponse.json();
  assert.equal(firstPage.exchanges.length, 50);
  assert.equal(firstPage.exchanges[0].id, 1);
  assert.equal(firstPage.exchanges[49].id, 50);
  assert.ok(firstPage.exchanges.every((exchange) => exchange.items.length === 3));
  assert.equal(firstPage.nextCursor, 50);
  assert.equal(firstPage.throughCursor, 51);
  assert.equal(firstPage.hasMore, true);

  env.addExchange(52);
  const secondResponse = await worker.fetch(
    new Request("https://worker.example/exchanges?status=pending&limit=50&after=50&through=51", {
      headers: { "X-Admin-Key": "secret" },
    }),
    env,
  );
  const secondPage = await secondResponse.json();
  assert.equal(secondPage.exchanges.length, 1);
  assert.equal(secondPage.exchanges[0].id, 51);
  assert.equal(secondPage.exchanges[0].items.length, 3);
  assert.equal(secondPage.nextCursor, null);
  assert.equal(secondPage.throughCursor, 51);
  assert.equal(secondPage.hasMore, false);
});

test("rejects malformed pending review cursors", async () => {
  const invalidAfter = await worker.fetch(
    new Request("https://worker.example/trades?status=pending&after=-1", {
      headers: { "X-Admin-Key": "secret" },
    }),
    { ...createTradeReviewListEnvironment(1), ADMIN_KEY: "secret" },
  );
  assert.equal(invalidAfter.status, 400);
  assert.deepEqual(await invalidAfter.json(), {
    ok: false,
    error: "Invalid cursor",
  });

  const invalidThrough = await worker.fetch(
    new Request("https://worker.example/trades?status=pending&through=invalid", {
      headers: { "X-Admin-Key": "secret" },
    }),
    { ...createTradeReviewListEnvironment(1), ADMIN_KEY: "secret" },
  );
  assert.equal(invalidThrough.status, 400);
  assert.deepEqual(await invalidThrough.json(), {
    ok: false,
    error: "Invalid cursor",
  });
});

test("records an authorized one-time exchange decision with review history", async () => {
  const batch = [];
  const env = {
    ADMIN_KEY: "secret",
    DB: {
      prepare(sql) {
        return {
          bind(...values) {
            return {
              sql,
              values,
              async first() {
                if (sql.includes("SELECT status FROM exchange_submissions")) {
                  return { status: "rejected" };
                }
                throw new Error(`Unexpected first query: ${sql}`);
              },
            };
          },
        };
      },
      async batch(statements) {
        batch.push(...statements);
        return [{ meta: { changes: 1 } }, { meta: { changes: 1 } }];
      },
    },
  };
  const response = await worker.fetch(
    new Request("https://worker.example/exchanges/23/status", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Admin-Key": "secret",
      },
      body: JSON.stringify({ status: "rejected", reviewerId: "discord-123" }),
    }),
    env,
  );

  assert.equal(response.status, 200);
  assert.equal(batch.length, 2);
  assert.match(batch[0].sql, /status = 'pending'/);
  assert.match(batch[1].sql, /exchange_review_actions/);
  assert.equal((await response.json()).status, "rejected");
});

test("requires administrator authentication for remote trade review", async () => {
  const response = await worker.fetch(
    new Request("https://worker.example/trades/7/status", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status: "approved", reviewerId: "discord-123" }),
    }),
    { ...createEnvironment(), ADMIN_KEY: "secret" },
  );

  assert.equal(response.status, 401);
  assert.deepEqual(await response.json(), {
    ok: false,
    error: "Unauthorized",
  });
});

test("does not permit moving a trade back to pending", async () => {
  const response = await worker.fetch(
    new Request("https://worker.example/trades/7/status", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Admin-Key": "secret",
      },
      body: JSON.stringify({ status: "pending", reviewerId: "discord-123" }),
    }),
    { ...createEnvironment(), ADMIN_KEY: "secret" },
  );

  assert.equal(response.status, 400);
  assert.deepEqual(await response.json(), {
    ok: false,
    error: "Invalid status",
  });
});

test("records an authorized pending-to-approved transition in one D1 batch", async () => {
  const batch = [];
  const env = {
    ADMIN_KEY: "secret",
    DB: {
      prepare(sql) {
        return {
          bind(...values) {
            const statement = { sql, values };
            statement.sql = sql;
            statement.values = values;
            return {
              ...statement,
              async first() {
                if (sql.includes("SELECT status FROM trades")) return { status: "approved" };
                throw new Error(`Unexpected first query: ${sql}`);
              },
            };
          },
        };
      },
      async batch(statements) {
        batch.push(...statements);
        return [{ meta: { changes: 1 } }, { meta: { changes: 1 } }, { meta: { changes: 1 } }];
      },
    },
  };
  const response = await worker.fetch(
    new Request("https://worker.example/trades/7/status", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Admin-Key": "secret",
      },
      body: JSON.stringify({ status: "approved", reviewerId: "discord-123" }),
    }),
    env,
  );
  assert.equal(response.status, 200);
  assert.equal(batch.length, 3);
  assert.match(batch[0].sql, /status = 'pending'/);
  assert.match(batch[2].sql, /trade_review_actions/);
  assert.match(batch[2].sql, /trade_review_actions/);
  const result = await response.json();
  assert.equal(result.ok, true);
  assert.equal(result.tradeId, 7);
  assert.equal(result.status, "approved");
  assert.ok(Number.isFinite(Date.parse(result.reviewedAt)));
});

test("rejects a second decision after the pending state was claimed", async () => {
  const env = {
    ADMIN_KEY: "secret",
    DB: {
      prepare() {
        return {
          bind() {
            return {
              async first() {
                return { status: "approved" };
              },
            };
          },
        };
      },
      async batch() {
        return [{ meta: { changes: 0 } }, { meta: { changes: 0 } }, { meta: { changes: 0 } }];
      },
    },
  };
  const response = await worker.fetch(
    new Request("https://worker.example/trades/7/status", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Admin-Key": "secret",
      },
      body: JSON.stringify({ status: "rejected", reviewerId: "discord-456" }),
    }),
    env,
  );

  assert.equal(response.status, 409);
  assert.deepEqual(await response.json(), {
    ok: false,
    error: "Trade has already been reviewed",
  });
});

test("keeps demand scores rounded and within the 0.0 to 5.0 range", () => {
  assert.equal(calculateDemandScore(0, 0, 0), 0);
  assert.equal(calculateDemandScore(5, 10, 10), 2.3);
  assert.equal(calculateDemandScore(10, 1, 100), 5);
  assert.throws(() => calculateDemandScore(-1, 1, 0), RangeError);
});

test("writes each daily snapshot and its mutations in one D1 batch", async () => {
  const batches = [];
  const queries = [];
  const env = {
    DB: {
      prepare(sql) {
        queries.push(sql);
        const all = async () => {
          if (sql.includes("SELECT DISTINCT tc.character_id")) {
            return { results: [{ character_id: "c002" }] };
          }
          if (sql.includes("SELECT tc.quantity, t.total_amount")) {
            return {
              results: [
                { total_amount: 100, quantity: 1 },
                { total_amount: 120, quantity: 1 },
                { total_amount: 140, quantity: 1 },
              ],
            };
          }
          if (sql.includes("WITH per_trade AS")) {
            return {
              results: [
                { mutation: "normal", sample_count: 3, multiplier: 1 },
                { mutation: "gold", sample_count: 2, multiplier: null },
              ],
            };
          }
          throw new Error(`Unexpected all query: ${sql}`);
        };
        const first = async () => {
          if (sql.includes("SELECT DISTINCT tc.character_id, t.id")) {
            return { count: 10 };
          }
          if (!sql.includes("COUNT(DISTINCT t.id)")) {
            throw new Error(`Unexpected first query: ${sql}`);
          }
          if (sql.includes("submitted_at")) return { count: 2 };
          return { count: 4 };
        };
        return {
          all,
          first,
          bind(...values) {
            return { sql, values, all, first };
          },
        };
      },
      async batch(statements) {
        batches.push(statements);
        return [{ meta: { last_row_id: 99 } }, ...statements.slice(1).map(() => ({}))];
      },
    },
  };
  let scheduled;
  await worker.scheduled({}, env, {
    waitUntil(promise) {
      scheduled = promise;
    },
  });
  const snapshots = await scheduled;

  assert.equal(snapshots.length, 1);
  assert.equal(snapshots[0].snapshotId, 99);
  assert.equal(batches.length, 1);
  assert.equal(batches[0].length, 3);
  assert.equal(queries.every((sql) => !sql.includes("exchange_submissions")), true);
  assert.match(batches[0][0].sql, /INSERT INTO market_snapshots/);
  assert.match(batches[0][0].sql, /level1_sample_count/);
  assert.deepEqual(batches[0][0].values.slice(-2), [3, 3]);
  assert.match(batches[0][1].sql, /INSERT INTO market_snapshot_mutations/);
  assert.match(batches[0][1].sql, /ORDER BY id DESC/);
  assert.match(
    queries.find((sql) => sql.includes("WITH per_trade AS")),
    /normal_levels\.level = mutations\.level/,
  );
  assert.equal(queries.filter((sql) => sql.includes("COUNT(DISTINCT t.id)")).length, 2);
  assert.ok(queries.some((sql) => sql.includes("SELECT DISTINCT tc.character_id, t.id")));
  assert.ok(queries.every((sql) => !sql.includes("t.status != 'approved'")));
});
