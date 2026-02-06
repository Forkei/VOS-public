# Phase 3: Building the Proactive VOS

**Objective:** To build the user-facing Virtual Operating System (VOS) and its underlying event-driven architecture. This phase shifts the focus from a purely conversational assistant to a fully interactive digital environment.

**Key Outcomes by End of Phase:**
- A functional Flutter application for web and desktop.
- The VOS backend publishes system-wide events to RabbitMQ.
- The first custom VOS application (Notes App) is built and is controllable by an agent.
- A basic proactive agent behavior is implemented.

---

### Tasks

#### 1. Implement the VOS Event Bus Architecture
- **Status:** [ ] Not Started
- **Task:** Configure RabbitMQ with an exchange to broadcast system-wide events.
- **Sub-Tasks:**
    - [ ] Create a "fanout" exchange in RabbitMQ named `vos_events`. All VOS events will be published here.
    - [ ] Update the API Gateway to publish events for key user actions (e.g., `user.session.login`, `user.application.open`).
    - [ ] Modify the `BaseAgent` class in the SDK to allow agents to subscribe to specific event patterns from this exchange.
- **Notes:** This is the foundation for proactive intelligence.

#### 2. Develop the VOS GUI with Flutter
- **Status:** [ ] Not Started
- **Task:** Build the main graphical user interface for the VOS.
- **Sub-Tasks:**
    - [ ] Set up the initial Flutter project.
    - [ ] Implement user authentication (login/logout screens).
    - [ ] Build the main "Desktop" interface:
        - [ ] An application launcher/dock.
        - [ ] A persistent chat window for interacting with the Primary Agent.
        - [ ] The ability to open applications in movable, resizable windows.
    - [ ] Integrate a WebSocket client to connect to the API Gateway for real-time communication.
- **Notes:** Focus on the core shell of the OS first.

#### 3. Build the First Custom VOS Application: Notes App
- **Status:** [ ] Not Started
- **Task:** Create a full-stack Notes application that exists within the VOS.
- **Sub-Tasks:**
    - [ ] **Frontend:** Build the Notes App UI in Flutter as a component that can be launched within the VOS shell.
    - [ ] **Backend:** Create a new microservice (`NotesAppService`) with a dedicated API for document management (e.g., `CRUD` operations on notes).
    - [ ] **API Exposure:** Expose the functions of the `NotesAppService` API as tools for the `NotesAgent`.
- **Notes:** This is the first proof-of-concept for agents and users sharing and interacting with the same application.

#### 4. Implement First Proactive Behavior
- **Status:** [ ] Not Started
- **Task:** Create a simple agent that reacts to system events instead of direct user commands.
- **Sub-Tasks:**
    - [ ] Create a new, simple agent (e.g., `GreeterAgent`).
    - [ ] Program this agent to subscribe to the `user.session.login` event.
    - [ ] When the event is detected, the agent should create a task for the Primary Agent to send a "Welcome back, [User]!" message in the chat.
- **Notes:** This tests the full event-driven loop, from a user action causing an event to an agent proactively responding.