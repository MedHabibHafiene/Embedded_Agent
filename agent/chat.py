"""
Chat mode - the first of the agent's two modes: documentation-grounded Q&A.

  Mode 1 (this module): retrieve from the indexed documents + core facts and
      answer questions about the board and toolchain. No code generation,
      no filesystem writes, no hardware access.
  Mode 2 (orchestrator.py): generate main.c, validate, compile, flash.

The engine is stateless - the caller (API or CLI) owns the conversation
history and passes it back in on each call, which keeps the server scalable
and restart-safe.
"""

import logging
from dataclasses import dataclass, field

from agent import llm
from config import CHAT_MAX_TOKENS, CHAT_TEMPERATURE, CHAT_TOP_K
from rag import Retriever

log = logging.getLogger(__name__)

CHAT_SYSTEM_PROMPT = """You are a knowledgeable assistant on everything STM32F4:
the STM32F407 microcontroller, its peripherals, registers, and the STM32F4 HAL
library - including the STM32F4-Discovery board (disco_f407vg, STM32F407VGT6)
used in this project.

Answer the user's questions using the retrieved documentation context that
accompanies each question. Rules:
- Ground every answer in the provided context; quote pin assignments,
  register names and configuration facts exactly as they appear there.
- If the context does not contain the answer, say so plainly, and only add
  general knowledge if you are confident - clearly marked as not coming
  from the provided documentation.
- Keep answers concise and technical; use short lists or code spans when
  they help.
- This is a chat, not a firmware generator: never output a complete main.c
  or flashing instructions unless the user explicitly asks for them.

When the user pastes embedded code and asks you to fix, debug or review it,
act as a senior embedded systems reviewer and follow this methodology:
1. Root cause first: explain why the current code fails (the HAL, interrupt
   or hardware behaviour behind the symptom), separating
   hardware/configuration issues from software issues. Never randomly
   rewrite working code without explaining why.
2. Give the minimal fix before any larger rewrite, then the
   production-quality version when it differs.
3. State clearly which file each piece of code belongs in: raw IRQ handlers
   go in stm32f4xx_it.c, HAL callbacks and application logic in main.c -
   and never duplicate an IRQ handler across files.
4. Systematically check the classic STM32 failure modes before answering:
   missing/duplicate IRQ handlers, a HAL_GPIO_EXTI_IRQHandler() trampoline
   not called or called with the wrong pin, wrong NVIC line grouping
   (EXTI0-4 vs EXTI9_5 vs EXTI15_10), blocking calls or HAL_Delay inside an
   ISR/callback, missing volatile on interrupt-shared flags, missing button
   debounce, wrong active-high/active-low assumption or wrong pull
   configuration, wrong PLL arithmetic, and HAL init return values ignored.
5. For timer and DMA code, additionally check: __HAL_LINKDMA argument shape
   (second argument is the peripheral's DMA-handle field such as
   hdma[TIM_DMA_ID_UPDATE], third is the bare handle variable - the macro
   applies & internally); timer Prescaler/Period must fit the counter width
   (TIM3/TIM4 are 16-bit on F407, TIM2/TIM5 are 32-bit) with
   update = TIMxCLK / ((PSC+1) x (ARR+1)) and TIMxCLK = 2x APB clock when
   the APB prescaler is not 1; HAL_TIM_Base_Start_DMA streams into
   TIMx->DMAR, NOT to GPIO - a pattern into GPIOx->BSRR needs
   HAL_DMA_Start_IT on the linked DMA handle plus enabling the update DMA
   request (__HAL_TIM_ENABLE_DMA) and the counter (__HAL_TIM_ENABLE); and
   "held for N seconds" must trigger
   while the button is still held (tick comparison in the main loop), not
   on release.
6. Apply resource-aware judgement: static allocation over malloc, no
   recursion in ISRs, fixed-width integer types, named constants over magic
   numbers, non-blocking timing when the firmware must stay responsive.
7. Cover the hardware considerations (pull-up/pull-down, active level,
   wiring, board-specific quirks) and finish with concrete steps to verify
   the fix on the board.
If the user has not provided needed hardware details (exact MCU, board,
pinout, active levels), state your assumptions explicitly and choose the
safest reasonable configuration."""


CHAT_QUESTION_TEMPLATE = """Retrieved documentation context:
{context}

Question: {question}"""


@dataclass
class ChatAnswer:
    """One chat turn: the question asked and the grounded answer given."""

    question: str
    answer: str
    sources: list[str] = field(default_factory=list)
    context: str = ""


def _sanitize_history(history: list[dict] | None) -> list[dict]:
    """Keep only well-formed user/assistant turns - anything else a client
    sends (empty content, tool roles, ...) is dropped, never forwarded."""
    cleaned = []
    for message in history or []:
        role = message.get("role")
        content = (message.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            cleaned.append({"role": role, "content": content})
    return cleaned


class ChatEngine:
    """Retrieval-augmented Q&A over the same store the generator uses."""

    def __init__(self, client=None, retriever: Retriever | None = None, top_k: int = CHAT_TOP_K):
        # Lazy like AgentOrchestrator: constructing the engine never touches
        # the LLM, so the server boots fine with Ollama down.
        self._client = client
        self._retriever = retriever
        self.top_k = top_k

    @property
    def client(self):
        if self._client is None:
            self._client = llm.create_client()
        return self._client

    @property
    def retriever(self) -> Retriever:
        if self._retriever is None:
            self._retriever = Retriever()
        return self._retriever

    def answer(self, question: str, history: list[dict] | None = None) -> ChatAnswer:
        """Answer one question, grounded in retrieved documentation.

        history: previous turns, oldest first, as
                 [{"role": "user"|"assistant", "content": "..."}].
        """
        retrieval = self.retriever.retrieve(question, top_k=self.top_k)

        messages = [{"role": "system", "content": CHAT_SYSTEM_PROMPT}]
        messages.extend(_sanitize_history(history))
        messages.append({
            "role": "user",
            "content": CHAT_QUESTION_TEMPLATE.format(
                context=retrieval.text or "(no documentation context retrieved)",
                question=question,
            ),
        })

        answer = llm.chat(
            self.client,
            messages,
            temperature=CHAT_TEMPERATURE,
            max_tokens=CHAT_MAX_TOKENS,
        )
        log.info(
            "Chat answered a %d-char question from %d source(s).",
            len(question),
            len(retrieval.sources),
        )
        return ChatAnswer(
            question=question,
            answer=answer,
            sources=retrieval.sources,
            context=retrieval.text,
        )
