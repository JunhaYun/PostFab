"""
코퍼스 레코드(카드/청크) -> 임베딩 입력 텍스트 변환. build_vectorstore.py와
07_train_embedding.py가 반드시 같은 텍스트를 써야 하므로 (학습 positive와 실제
검색 대상 벡터가 어긋나면 파인튜닝 효과가 사라짐) 여기 한 곳에서만 관리한다.
"""


def build_card_text(card: dict) -> str:
    alias = f" ({', '.join(card['aliases'])})" if card["aliases"] else ""
    return f"{card['term']}{alias}: {card['definition']}"


def build_chunk_text(chunk: dict) -> str:
    return f"{chunk['context_header']}\n{chunk['text']}"
