"""Customer-facing copy for controlled (non-generated) responses, kept in one place.

None of these state company facts; they only explain what the assistant is doing.
"""

ABSTAIN = (
    "I couldn't find enough information in our knowledge base to answer that reliably, "
    "and I don't want to guess. Would you like me to connect you with a support representative?"
)
CLARIFY = "Could you tell me a bit more about what you need help with?"
GREETING = (
    "Hi! I'm the virtual support assistant. Ask me a question and I'll answer from our "
    "support documentation, or ask for a person at any time."
)
THANKS = "You're welcome! Is there anything else I can help you with?"
REFUSAL_INJECTION = "I can't help with that request. I can answer questions about our products, services and policies."
HANDOFF_USER_REQUESTED = (
    "You're being connected to a support representative. They'll be able to see this "
    "conversation, so you won't need to repeat yourself."
)
HANDOFF_CONFLICT = (
    "I found conflicting information in our documentation, so I don't want to give you an "
    "inaccurate answer. You're being connected to a support representative who can confirm."
)
HANDOFF_SENSITIVE = (
    "This is something a member of our support team should handle directly. "
    "You're being connected to a support representative."
)
HANDOFF_SECURITY = (
    "I'm sorry to hear that - security concerns need a person's attention right away. "
    "You're being connected to a support representative. Please don't share passwords or full card numbers in this chat."
)
HANDOFF_ACCOUNT = (
    "I can't access account or order details. You're being connected to a support "
    "representative who can look into this for you."
)
HANDOFF_FRUSTRATED = "I'm sorry this has been frustrating. You're being connected to a support representative."
HANDOFF_DISPUTE = (
    "Thanks for flagging that. You're being connected to a support representative who can "
    "review the information with you."
)
HANDOFF_REPEATED_FAILURES = (
    "I still couldn't find a reliable answer in our documentation. You're being connected to a support representative."
)
HANDOFF_VALIDATION = (
    "I wasn't able to verify an answer against our documentation, so I'm not going to guess. "
    "You're being connected to a support representative."
)
HANDOFF_AI_ESCALATION = "You're being connected to a support representative who can help with this."
PROVIDER_UNAVAILABLE = (
    "The AI support service is temporarily unavailable. Please try again or contact a support representative."
)
PROVIDER_UNAVAILABLE_HANDOFF = (
    "The AI support service is temporarily unavailable, so I can't answer reliably right now. "
    "You're being connected to a support representative."
)
RETRIEVAL_UNAVAILABLE = (
    "Our knowledge base is temporarily unavailable, so I can't answer reliably right now. "
    "You're being connected to a support representative."
)
HUMAN_OWNS_CONVERSATION = "A support representative has this conversation and will reply here."
