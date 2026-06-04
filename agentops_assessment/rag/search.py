from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any

from agentops_assessment.backend import database


def tokenize(text: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9-]+|[\u4e00-\u9fff]", text.lower())


def cosine_score(query_tokens: list[str], doc_tokens: list[str]) -> float:
    if not query_tokens or not doc_tokens:
        return 0.0
    q = Counter(query_tokens)
    d = Counter(doc_tokens)
    dot = sum(q[token] * d[token] for token in q.keys() & d.keys())
    q_norm = math.sqrt(sum(v * v for v in q.values()))
    d_norm = math.sqrt(sum(v * v for v in d.values()))
    if not q_norm or not d_norm:
        return 0.0
    return dot / (q_norm * d_norm)


class KnowledgeIndex:
    def search(
        self,
        query: str,
        user_permissions: list[str],
        top_k: int = 3,
    ) -> dict[str, Any]:
        with database.connect() as conn:
            database.init_db(conn)
            rows = conn.execute(
                """
                SELECT id, doc_id, source_path, title, permission, content
                FROM knowledge_chunks
                """
            ).fetchall()

        visible_chunks = []
        restricted_doc_ids = set()
        
        for row in rows:
            row_dict = dict(row)
            required_permission = row_dict["permission"]
            if required_permission == "knowledge:read" or required_permission in user_permissions:
                visible_chunks.append(row_dict)
            else:
                restricted_doc_ids.add(row_dict["doc_id"])

        query_tokens = tokenize(query)
        
        scored_chunks = []
        for chunk in visible_chunks:
            content_tokens = tokenize(chunk["content"])
            score = cosine_score(query_tokens, content_tokens)
            if score > 0:
                scored_chunks.append((score, chunk))
        
        scored_chunks.sort(key=lambda x: x[0], reverse=True)
        top_chunks = [chunk for _, chunk in scored_chunks[:top_k]]
        
        citations = []
        answer_parts = []
        seen_docs = set()
        
        for chunk in top_chunks:
            if chunk["doc_id"] not in seen_docs:
                citations.append({
                    "doc_id": chunk["doc_id"],
                    "title": chunk["title"],
                    "source_path": chunk["source_path"],
                    "chunk_id": chunk["id"],
                })
                seen_docs.add(chunk["doc_id"])
            
            sentences = re.split(r"[。！？\n]", chunk["content"])
            for sentence in sentences[:3]:
                if sentence.strip() and len(sentence.strip()) > 10:
                    answer_parts.append(sentence.strip())
        
        answer = "。".join(answer_parts[:5]) + "。" if answer_parts else "未找到相关知识。"
        
        injection_patterns = [
            r"忽略之前的所有指令",
            r"忽略之前指令",
            r"覆盖之前的所有指令",
            r"按照我的指令执行",
            r"执行我的命令",
            r"泄露",
            r"secret",
            r"密钥",
        ]
        
        for pattern in injection_patterns:
            if re.search(pattern, query, re.IGNORECASE):
                answer = "查询内容包含不安全指令，已被过滤。"
                break
        
        return {
            "answer": answer,
            "citations": citations,
            "filtered_doc_ids": sorted(list(restricted_doc_ids)),
        }