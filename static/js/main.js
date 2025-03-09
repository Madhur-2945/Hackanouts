/**
 * Main JavaScript file for the Resume Builder Application
 * Contains functionality for various resume features
 */

document.addEventListener('DOMContentLoaded', function() {
    // Initialize tooltips
    var tooltipTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]'));
    tooltipTriggerList.map(function (tooltipTriggerEl) {
        return new bootstrap.Tooltip(tooltipTriggerEl);
    });

    // Handle flash messages
    setTimeout(function() {
        const flashMessages = document.querySelectorAll('.alert-dismissible');
        flashMessages.forEach(function(message) {
            bootstrap.Alert.getInstance(message)?.close();
        });
    }, 5000);

    // Resume Chat Functionality
    initializeChat();

    // Real-time grammar checking
    initializeGrammarChecker();

    // Resume section management
    initializeSectionManager();
});

/**
 * Initialize resume chat functionality
 */
function initializeChat() {
    const chatForm = document.getElementById('chat-form');
    const messagesContainer = document.getElementById('messages-container');
    const userInput = document.getElementById('user-input');
    
    if (!chatForm) return;
    
    chatForm.addEventListener('submit', function(e) {
        e.preventDefault();
        
        const message = userInput.value.trim();
        if (!message) return;
        
        // Add user message to the chat
        addMessage('user', message);
        
        // Clear the input
        userInput.value = '';
        
        // Get resume ID from the form
        const resumeId = chatForm.dataset.resumeId;
        
        // Send request to server
        fetch('/api/get_response', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                prompt: message,
                resume_id: resumeId
            }),
        })
        .then(response => response.json())
        .then(data => {
            if (data.error) {
                addMessage('assistant', 'Sorry, there was an error: ' + data.error);
            } else {
                addMessage('assistant', data.response);
            }
            // Scroll to bottom
            scrollToBottom();
        })
        .catch(error => {
            console.error('Error:', error);
            addMessage('assistant', 'Sorry, there was an error processing your request.');
            scrollToBottom();
        });
    });
    
    // Function to add a message to the chat
    function addMessage(role, content) {
        const messageDiv = document.createElement('div');
        messageDiv.classList.add('message', role);
        
        // Format the content with markdown
        const formattedContent = content.replace(/\n/g, '<br>');
        messageDiv.innerHTML = formattedContent;
        
        messagesContainer.appendChild(messageDiv);
        scrollToBottom();
    }
    
    // Function to scroll the chat to the bottom
    function scrollToBottom() {
        messagesContainer.scrollTop = messagesContainer.scrollHeight;
    }
    
    // Initialize by scrolling to the bottom
    scrollToBottom();
    
    // Make the textarea resizable but with max height
    if (userInput) {
        userInput.addEventListener('input', function() {
            this.style.height = 'auto';
            this.style.height = (this.scrollHeight) + 'px';
            if (this.scrollHeight > 200) {
                this.style.overflowY = 'auto';
                this.style.height = '200px';
            } else {
                this.style.overflowY = 'hidden';
            }
        });
    }
}

/**
 * Initialize real-time grammar checker
 */
function initializeGrammarChecker() {
    const sectionInputs = document.querySelectorAll('.section-editor textarea');
    const checkGrammarBtn = document.getElementById('check-grammar-btn');
    
    if (!checkGrammarBtn) return;
    
    checkGrammarBtn.addEventListener('click', function() {
        // Collect text from all sections
        let allText = '';
        sectionInputs.forEach(input => {
            allText += input.value + '\n\n';
        });
        
        // Start the grammar check
        checkGrammar(allText);
    });
    
    function checkGrammar(text) {
        showLoadingSpinner();
        
        fetch('/api/check_grammar', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ text: text }),
        })
        .then(response => response.json())
        .then(data => {
            hideLoadingSpinner();
            if (data.error) {
                showAlert('Error: ' + data.error, 'danger');
            } else {
                displayGrammarResults(data.result);
            }
        })
        .catch(error => {
            hideLoadingSpinner();
            console.error('Error:', error);
            showAlert('Failed to check grammar: ' + error.message, 'danger');
        });
    }
    
    function displayGrammarResults(results) {
        const resultsContainer = document.getElementById('grammar-results');
        if (!resultsContainer) return;
        
        resultsContainer.innerHTML = '';
        resultsContainer.classList.remove('d-none');
        
        // Create results heading
        const heading = document.createElement('h4');
        heading.textContent = 'Grammar Check Results';
        resultsContainer.appendChild(heading);
        
        // Format and display the results
        const resultContent = document.createElement('div');
        resultContent.innerHTML = results.replace(/\n/g, '<br>');
        resultContent.classList.add('grammar-feedback');
        resultsContainer.appendChild(resultContent);
        
        // Add close button
        const closeBtn = document.createElement('button');
        closeBtn.textContent = 'Close';
        closeBtn.classList.add('btn', 'btn-secondary', 'mt-3');
        closeBtn.addEventListener('click', function() {
            resultsContainer.classList.add('d-none');
        });
        resultsContainer.appendChild(closeBtn);
        
        // Scroll to results
        resultsContainer.scrollIntoView({ behavior: 'smooth' });
    }
}

/**
 * Initialize section management functionality
 */
function initializeSectionManager() {
    const addSectionBtn = document.getElementById('add-section-btn');
    const sectionContainer = document.getElementById('sections-container');
    
    if (!addSectionBtn || !sectionContainer) return;
    
    // Add section event
    addSectionBtn.addEventListener('click', function() {
        const sectionName = prompt('Enter section name:');
        if (!sectionName) return;
        
        const resumeId = this.dataset.resumeId;
        
        fetch('/api/add_section', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                resume_id: resumeId,
                section_name: sectionName
            }),
        })
        .then(response => response.json())
        .then(data => {
            if (data.error) {
                showAlert('Error: ' + data.error, 'danger');
            } else {
                // Reload the page to show the new section
                location.reload();
            }
        })
        .catch(error => {
            console.error('Error:', error);
            showAlert('Failed to add section: ' + error.message, 'danger');
        });
    });
    
    // Delete section events
    document.querySelectorAll('.delete-section-btn').forEach(button => {
        button.addEventListener('click', function(e) {
            e.preventDefault();
            
            if (!confirm('Are you sure you want to delete this section? This cannot be undone.')) {
                return;
            }
            
            const sectionId = this.dataset.sectionId;
            
            fetch('/api/delete_section', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    section_id: sectionId
                }),
            })
            .then(response => response.json())
            .then(data => {
                if (data.error) {
                    showAlert('Error: ' + data.error, 'danger');
                } else {
                    // Remove the section from the DOM
                    const sectionElem = document.getElementById('section-' + sectionId);
                    if (sectionElem) {
                        sectionElem.remove();
                    }
                    showAlert('Section deleted successfully', 'success');
                }
            })
            .catch(error => {
                console.error('Error:', error);
                showAlert('Failed to delete section: ' + error.message, 'danger');
            });
        });
    });
    
    // Reorder section functionality
    const moveUpButtons = document.querySelectorAll('.move-section-up');
    const moveDownButtons = document.querySelectorAll('.move-section-down');
    
    moveUpButtons.forEach(button => {
        button.addEventListener('click', function() {
            const sectionElement = this.closest('.section-editor');
            const previousSection = sectionElement.previousElementSibling;
            
            if (previousSection && previousSection.classList.contains('section-editor')) {
                sectionContainer.insertBefore(sectionElement, previousSection);
                updateSectionOrder();
            }
        });
    });
    
    moveDownButtons.forEach(button => {
        button.addEventListener('click', function() {
            const sectionElement = this.closest('.section-editor');
            const nextSection = sectionElement.nextElementSibling;
            
            if (nextSection && nextSection.classList.contains('section-editor')) {
                sectionContainer.insertBefore(nextSection, sectionElement);
                updateSectionOrder();
            }
        });
    });
    
    function updateSectionOrder() {
        const sections = Array.from(document.querySelectorAll('.section-editor'));
        const resumeId = addSectionBtn.dataset.resumeId;
        
        const sectionIds = sections.map(section => section.dataset.sectionId);
        
        fetch('/api/update_sections_order', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                resume_id: resumeId,
                section_ids: sectionIds
            }),
        })
        .then(response => response.json())
        .then(data => {
            if (data.error) {
                console.error('Error updating section order:', data.error);
            }
        })
        .catch(error => {
            console.error('Error:', error);
        });
    }
}

/**
 * Show loading spinner
 */
function showLoadingSpinner() {
    const spinner = document.getElementById('loading-spinner');
    if (spinner) {
        spinner.classList.remove('d-none');
    }
}

/**
 * Hide loading spinner
 */
function hideLoadingSpinner() {
    const spinner = document.getElementById('loading-spinner');
    if (spinner) {
        spinner.classList.add('d-none');
    }
}

/**
 * Show alert message
 */
function showAlert(message, type = 'info') {
    const alertContainer = document.getElementById('alert-container');
    if (!alertContainer) return;
    
    const alertElement = document.createElement('div');
    alertElement.classList.add('alert', `alert-${type}`, 'alert-dismissible', 'fade', 'show');
    alertElement.setAttribute('role', 'alert');
    
    alertElement.innerHTML = `
        ${message}
        <button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>
    `;
    
    alertContainer.appendChild(alertElement);
    
    // Auto-dismiss after 5 seconds
    setTimeout(() => {
        const bootstrapAlert = new bootstrap.Alert(alertElement);
        bootstrapAlert.close();
    }, 5000);
}

/**
 * ATS Optimization functionality
 */
function initializeATSOptimizer() {
    const optimizeATSBtn = document.getElementById('optimize-ats-btn');
    const jobDescriptionInput = document.getElementById('job-description');
    
    if (!optimizeATSBtn || !jobDescriptionInput) return;
    
    optimizeATSBtn.addEventListener('click', function() {
        const jobDescription = jobDescriptionInput.value.trim();
        if (!jobDescription) {
            showAlert('Please enter a job description for ATS optimization', 'warning');
            return;
        }
        
        // Collect resume text
        const sectionInputs = document.querySelectorAll('.section-editor textarea');
        let resumeText = '';
        sectionInputs.forEach(input => {
            resumeText += input.value + '\n\n';
        });
        
        showLoadingSpinner();
        
        fetch('/api/optimize_ats', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                resume_text: resumeText,
                job_description: jobDescription
            }),
        })
        .then(response => response.json())
        .then(data => {
            hideLoadingSpinner();
            if (data.error) {
                showAlert('Error: ' + data.error, 'danger');
            } else {
                displayATSResults(data.result);
            }
        })
        .catch(error => {
            hideLoadingSpinner();
            console.error('Error:', error);
            showAlert('Failed to optimize for ATS: ' + error.message, 'danger');
        });
    });
    
    function displayATSResults(results) {
        const resultsContainer = document.getElementById('ats-results');
        if (!resultsContainer) return;
        
        resultsContainer.innerHTML = '';
        resultsContainer.classList.remove('d-none');
        
        // Create results heading
        const heading = document.createElement('h4');
        heading.textContent = 'ATS Optimization Results';
        resultsContainer.appendChild(heading);
        
        // Format and display the results
        const resultContent = document.createElement('div');
        resultContent.innerHTML = results.replace(/\n/g, '<br>');
        resultContent.classList.add('ats-feedback');
        resultsContainer.appendChild(resultContent);
        
        // Add close button
        const closeBtn = document.createElement('button');
        closeBtn.textContent = 'Close';
        closeBtn.classList.add('btn', 'btn-secondary', 'mt-3');
        closeBtn.addEventListener('click', function() {
            resultsContainer.classList.add('d-none');
        });
        resultsContainer.appendChild(closeBtn);
        
        // Scroll to results
        resultsContainer.scrollIntoView({ behavior: 'smooth' });
    }
}

// Initialize all components on load
document.addEventListener('DOMContentLoaded', function() {
    initializeChat();
    initializeGrammarChecker();
    initializeSectionManager();
    initializeATSOptimizer();
});