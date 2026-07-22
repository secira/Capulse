# Capulse Portfolio Preferences Form - Design Guidelines

## Design Approach
**Reference-Based with Financial Services Standards**: Drawing from getquin.com's clean card-based layouts and arvat.ai's professional form patterns, combined with established financial dashboard principles (Stripe, Plaid, Robinhood). Focus on trust-building through clarity, generous spacing, and guided progression.

## Core Design Elements

### Typography System
**Font Family**: Poppins (Google Fonts)
- Headings: Poppins SemiBold (600) - 32px page title, 24px section headers, 18px step titles
- Body: Poppins Regular (400) - 16px labels, 15px input text, 14px helper text
- Accent: Poppins Medium (500) - 16px button text, 14px navigation items

### Color Palette
- **Primary Navy**: #00091a (navigation, primary buttons, active states)
- **Backgrounds**: Pure white (#ffffff) main content, #f8f9fb section backgrounds
- **Text Hierarchy**: #1a1a1a headings, #4a4a4a body, #6b7280 helper text
- **Accents**: #10b981 success/validation, #ef4444 errors, #f59e0b warnings
- **Borders**: #e5e7eb default, #00091a focused inputs

### Layout System
**Container Structure**: max-w-4xl centered container with px-6 md:px-8 for form content
**Spacing Units**: Tailwind 4, 6, 8, 12, 16, 24 - maintain consistent rhythm
- Form sections: mb-12
- Between fields: mb-6
- Field groups: mb-8
- Step content: py-12

## Navigation & Header

**Top Navigation Bar**:
- Fixed position with subtle shadow (shadow-sm)
- Background: #00091a
- Height: 64px
- Inner container: max-w-7xl with px-6
- Left: Capulse logo (white) with "Portfolio Preferences" breadcrumb
- Right: User avatar (40px circular), "Save Draft" text button, "Help" icon button
- Use white text and icons against navy background

## Multi-Step Form Structure

**Progress Indicator**:
- Horizontal step indicator below navigation (bg-white with border-b)
- 5 steps displayed as connected nodes with labels
- Active step: filled navy circle with white text
- Completed: green checkmark in circle
- Upcoming: gray outline circles
- Line connecting steps: gray for incomplete, navy for completed segments
- Step labels beneath circles (14px Poppins Medium)

**Steps Layout**:
1. Personal Information
2. Financial Goals
3. Risk Assessment
4. Investment Preferences
5. Review & Submit

## Form Components Library

### Input Fields
**Text Inputs**:
- Height: 48px
- Border: 1.5px solid #e5e7eb, rounded-lg
- Focused: border #00091a with subtle shadow
- Label above: 14px Poppins Medium, mb-2
- Padding: px-4
- Placeholder: #9ca3af

**Select Dropdowns**:
- Match text input styling
- Custom chevron icon (navy)
- Dropdown menu: white bg, shadow-lg, rounded-lg
- Options: hover state with light gray bg (#f3f4f6)

**Radio/Checkbox Groups**:
- Custom styled with navy accent color
- Card-based selection for major choices (border, hover shadow)
- Inline options for simple choices
- Active card: navy border (2px) with light navy bg tint

**Range Sliders**:
- For risk tolerance and allocation percentages
- Track: 6px height, light gray background
- Filled portion: navy gradient
- Thumb: 20px circle, navy with shadow
- Value labels at both ends and current position

### Card Components

**Section Cards**:
- Background: white
- Border: 1px solid #e5e7eb
- Border radius: 12px
- Padding: p-8
- Shadow: shadow-sm on default, shadow-md on hover
- Use for grouping related fields (e.g., "Investment Timeline" section)

**Selection Cards** (for choosing goals, strategies):
- Grid layout: grid-cols-1 md:grid-cols-2 gap-6
- Each card: p-6, cursor-pointer
- Icon at top (32px), title (18px SemiBold), description (14px)
- Unselected: border-gray-200
- Selected: border-navy (2px), light navy bg tint (#f0f4ff)

### Buttons & Actions

**Primary Button**:
- Background: #00091a
- Text: white, 16px Poppins Medium
- Height: 48px, px-8
- Rounded: rounded-lg
- Shadow: shadow-sm

**Secondary Button**:
- Background: white
- Border: 1.5px solid #00091a
- Text: #00091a, 16px Poppins Medium
- Same dimensions as primary

**Navigation Buttons** (bottom of each step):
- "Previous" (secondary) on left
- "Next" or "Continue" (primary) on right
- Space between: justify-between
- Container: sticky bottom with py-6 bg-white border-t

## Form Step Content

### Step 1: Personal Information
- 2-column grid on desktop (md:grid-cols-2 gap-6)
- Fields: Full Name, Email, Phone, Date of Birth, Country, Annual Income bracket
- Income as select dropdown with ranges

### Step 2: Financial Goals
- Selection cards in 2-column grid
- Options: Retirement Planning, Wealth Building, Income Generation, Capital Preservation, Education Funding
- Multi-select allowed with checkmarks
- Follow-up fields based on selection (e.g., retirement age, target amount)
- Timeline slider for each selected goal

### Step 3: Risk Assessment
- Visual risk spectrum slider (Conservative to Aggressive)
- 5-point scale with labels and descriptions
- Interactive questionnaire cards (4-6 questions)
- Question format: card with question text, radio options as styled buttons
- Real-time risk score visualization (circular progress indicator)

### Step 4: Investment Preferences
- Asset allocation sliders grouped in card
- Preferred sectors: checkbox grid (Technology, Healthcare, Finance, etc.)
- ESG preferences: toggle switches for categories
- Geographic preferences: multi-select dropdown
- Rebalancing frequency: radio cards

### Step 5: Review & Submit
- Summary cards for each previous section
- Editable with "Edit" button in top-right of each summary card
- Confirmation checkboxes for terms, disclosures
- Large primary "Submit Portfolio Preferences" button
- Helper text: "You can modify these preferences anytime from your dashboard"

## Validation & Feedback

**Real-time Validation**:
- Success: green checkmark icon right-side of input, green border tint
- Error: red exclamation icon, red border, error message below in red (14px)
- Warning: yellow alert icon for recommendations

**Progress Saving**:
- Auto-save indicator in top-right: "All changes saved" with green dot
- Draft save timestamp

**Help & Guidance**:
- Info icons (i) next to complex fields
- Tooltip on hover with white bg, shadow-lg, max-w-xs
- Contextual help panel (optional slide-out from right)

## Responsive Behavior
- Desktop (1024px+): 2-column layouts, full horizontal progress
- Tablet (768-1023px): Single column forms, compact progress indicator
- Mobile (<768px): Stack everything, simplified step indicator (e.g., "Step 2 of 5")

## Visual Enhancements
- Subtle shadows throughout for depth without clutter
- Smooth transitions (150ms) on hovers and focus states
- Micro-interactions: checkmark animations, slider movements
- Loading states: skeleton screens for data fetching
- Success state after submission: confetti animation, success card with "Portfolio Created" message

## Images
No hero image required for this form-focused interface. Consider small illustrative icons (60-80px) for each step header to add visual interest and aid comprehension.