Based on the video, the speaker (Bohdan) has developed an automated system using **Claude Code** and **Claude Sonnet AI** to generate highly personalized Upwork proposals at scale. He calls this his **Upwork Skill Pipeline**. 

Here is the exact 4-step proposal automation process extracted from the video:

### **Step 1: Scrape Jobs**
* **The Tool:** He uses **Apify** (an automated web scraper) to connect to Upwork via API.
* **The Input:** He feeds the system specific keywords (e.g., AI, chatbot, n8n, appointment setter).
* **The Output:** In about 30 seconds, it pulls up to 20 fresh job postings along with all their details (job title, description, budget, client rating, client spend, and verification status).
* **Filters:** He filters out jobs based on the age of the post, minimum budget, and payment-verified clients.

### **Step 2: Generate Proposals**
* **The Tool:** Claude AI (Claude Sonnet).
* **Smart Contact Name Discovery:** The AI scans the job description to find the client's name or company mentioned in past reviews or signature lines (e.g., "Thanks, Sarah") to personalize the greeting.
* **Technical Proposal Creation:** Claude reads the entire job description and writes a detailed proposal in a direct, conversational, first-person tone. It includes a custom execution plan, technical reasoning, deliverables, and a project timeline. 
* **Visual Diagram Generation:** Claude generates a max 6-node **Mermaid flowchart** (a visual diagram mapping out the solution). The system renders this diagram as an image.
* **Centralization:** All of this detailed proposal data and the visual diagram are automatically compiled into a clean, professional Google Doc.

### **Step 3: Create the Short Cover Letter**
* **The Rule:** The system generates a cover letter that is **strictly 35 words or less**.
* **The Reason:** 35 words is the maximum limit that fits "above the fold" in the Upwork freelancer preview window. This ensures the client sees the best part without having to click "view more."
* **The Structure:** It includes a confident opener acknowledging the problem and a direct link to the full Google Doc proposal generated in Step 2.

### **Step 4: Output to Google Sheet**
* **The Dashboard:** All of the data from the previous steps automatically populates into a centralized Google Sheet.
* **The Columns:** The sheet displays the Job Title, Budget, Client Rating, the 35-word Cover Letter, the Proposal Google Doc link, and a **One-Click Apply Link**.
* **The Manual Execution:** The speaker's manual workload is reduced to opening the sheet, scanning the opportunities, clicking the apply link, pasting the pre-written cover letter (which contains the deep-dive Google Doc link), and hitting submit.

***

### **Efficiency & Cost Summary**
* **Time saved:** Instead of taking 20 to 60 minutes to research and write one high-quality manual proposal, this system generates **20 tailored proposals in 5 to 10 minutes**.
* **Cost:** It costs roughly **$2 to $3 in API fees** (Apify + Anthropic) to generate a batch of 20 high-quality proposals.