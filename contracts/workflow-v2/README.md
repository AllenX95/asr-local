# Workflow v2 contract assets

这些 schema 和 fixture 是 JSONL worker、Rust desktop adapter、Python supervisor 与 TypeScript runtime 共用的语言无关地基。

- schema 先定义 envelope、Draft、Spec、Snapshot、Event、Error 和 artifact/secret 使用的核心字段。
- fixture 不包含真实 API key、完整录音文本或模型权重路径之外的敏感信息。
- canonical operation digest 的实现必须先做 schema normalization，再按 RFC 8785 JCS 计算 SHA-256；fixture 本身不把 digest 作为实现语言的隐式默认值。
- v1 lane fixture 继续保留在各自 v1 测试中，Phase 1 不改写 v1 运行链。
