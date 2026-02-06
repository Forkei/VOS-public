# Phase 4: Advanced Intelligence & Security

**Objective:** To layer on the most sophisticated and critical features of the system, including robust security, advanced agent capabilities, and natural voice interaction.

**Key Outcomes by End of Phase:**
- A Watchdog Agent capable of monitoring and vetoing high-risk actions.
- A Proactive Memory system that automatically manages agent memories.
- A functional Browser-Use Agent for web automation.
- Hands-free voice command and real-time "Call Mode" are implemented.

---

### Tasks

#### 1. Implement the Watchdog Agent
- **Status:** [ ] Not Started
- **Task:** Build a high-privilege, independent agent to act as a security supervisor.
- **Sub-Tasks:**
    - [ ] Create the `WatchdogAgent` service.
    - [ ] Design a "proxy" or "approval" workflow. Before a high-risk tool is executed (e.g., by the Browser-Use Agent), the request must first be sent to the Watchdog.
    - [ ] The Watchdog will use an LLM call or rule-based system to assess risk and either approve or deny the action.
    - [ ] Create a "Security Panel" in the VOS GUI to display alerts and logs from the Watchdog.
- **Notes:** Security is paramount. This agent operates outside the normal agent hierarchy.

#### 2. Develop the Proactive Memory System
- **Status:** [ ] Not Started
- **Task:** Create two background agents that manage memory automatically.
- **Sub-Tasks:**
    - [ ] **Memory Creation Agent:** This agent subscribes to conversation events, periodically analyzes the recent turn history, and autonomously creates relevant memories in Weaviate.
    - [ ] **Memory Retrieval Agent:** This agent analyzes the current user input/context and proactively searches Weaviate for relevant memories, feeding them into the Primary Agent's context for its next turn.
- **Notes:** This offloads the cognitive burden from the main agents, making memory feel like a natural subconscious.

#### 3. Build the Browser-Use Agent
- **Status:** [ ] Not Started
- **Task:** Create an agent capable of controlling a web browser to perform tasks.
- **Sub-Tasks:**
    - [ ] Set up a service with browser automation libraries like **Selenium** or **Playwright**.
    - [ ] Define a set of primitive browser tools (e.g., `goto_url`, `click_element`, `type_in_field`).
    - [ ] Develop a sophisticated prompting strategy that allows the agent to break down a high-level goal (e.g., "order a pizza") into a sequence of these tool calls.
    - [ ] **CRITICAL:** Ensure all actions from this agent are routed through the Watchdog Agent for approval.
- **Notes:** This is one of the most powerful and highest-risk components of the system.

#### 4. Integrate Voice and Call Mode
- **Status:** [ ] Not Started
- **Task:** Add voice interaction capabilities to the VOS.
- **Sub-Tasks:**
    - [ ] **Wake Word & Voice Commands:** Integrate the Gemini API's speech-to-text capabilities into the Flutter client to handle hands-free commands.
    - [ ] **Call Mode:**
        - [ ] Implement a low-latency, real-time audio streaming system using **WebRTC**.
        - [ ] Integrate a fast text-to-speech API (e.g., Google TTS, ElevenLabs) on the backend.
        - [ ] Create a UI in Flutter to initiate and manage the "call" with the Primary Agent.
- **Notes:** Low latency is the key to making this feel natural and not clunky.