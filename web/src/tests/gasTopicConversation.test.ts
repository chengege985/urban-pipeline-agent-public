import { describe, expect, it } from "vitest";
import { GAS_TOPIC_QUESTION_LIMIT, topicFollowUp } from "../features/gas/topicContract";

const facts = ["登记版本", "合成登记材质为 PE。", "实际供气未知。"];
const previous = { question: "这个管段是什么材质？", answer: { facts, answer: facts.join("\n") } };

describe("gas topic follow-up", () => {
  it("sends an initial question without conversation context", () => {
    expect(topicFollowUp("  有哪些登记资料？  ")).toBe("有哪些登记资料？");
  });

  it("binds a follow-up to the preceding question and its selected facts", () => {
    const prompt = topicFollowUp("那管径呢？", previous);
    expect(prompt).toContain(previous.question + "\n" + facts[1]);
    expect(prompt).toMatch(/当前追问\n那管径呢？$/);
    expect(prompt).not.toContain(facts[0]);
  });

  it("keeps the complete current question within the contract limit", () => {
    const question = "管".repeat(GAS_TOPIC_QUESTION_LIMIT - 20) + "径？";
    const prompt = topicFollowUp(question, { ...previous, question: "🙂".repeat(600) });
    expect([...prompt]).toHaveLength(GAS_TOPIC_QUESTION_LIMIT);
    expect(prompt.endsWith(question)).toBe(true);
    expect(prompt).not.toMatch(/[\uD800-\uDBFF](?![\uDC00-\uDFFF])/);
  });
});
