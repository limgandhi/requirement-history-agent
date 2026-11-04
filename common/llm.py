"""Bedrock LLM / embedding factories. Fallback switching is added later (T14)."""
from langchain_aws import BedrockEmbeddings, ChatBedrockConverse

from common import config


def get_llm(model_id: str = config.LLM_MODEL_ID) -> ChatBedrockConverse:
    return ChatBedrockConverse(
        model=model_id,
        region_name=config.AWS_REGION,
        temperature=config.LLM_TEMPERATURE,
    )


def get_embeddings() -> BedrockEmbeddings:
    return BedrockEmbeddings(
        model_id=config.EMBEDDING_MODEL_ID,
        region_name=config.AWS_REGION,
        model_kwargs={"dimensions": config.EMBEDDING_DIMENSIONS},
    )
